from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import LLMConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import load_prompt
from .schemas import LLMMessage, PhaseTransition, SkillPhase, ToolCallRecord
from .tools import Toolbox

_TRUNCATION_MARKER = "\n\n[truncated]\n"
_OLDER_MESSAGE_LIMIT = 1600
_MIN_MESSAGE_LIMIT = 600
_RECENT_TOOL_RESULTS_TO_KEEP = 3
_SEMANTIC_FAILURE_MARKERS = (
    "failed to call model",
    "unknown mode",
    "traceback",
    "exception",
    "no such file",
    "not found",
    "does not exist",
    "permission denied",
    "missing required positional argument",
    "missing required positional arguments",
)

_TAG_RE = re.compile(r"<(THOUGHT|CALL|ARGS|NEXT|ANSWER)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)


@dataclass
class ParsedAction:
    thought: str = ""
    action_type: str = ""  # "call", "next", "answer", "none"
    tool_name: str = ""
    tool_args: dict = None
    next_phase: str = ""
    answer: str = ""
    raw_text: str = ""


def parse_executor_tags(text: str) -> ParsedAction:
    """Parse tag-based executor output into a structured action."""
    result = ParsedAction(raw_text=text)
    tags: dict[str, str] = {}
    for match in _TAG_RE.finditer(text):
        tag_name = match.group(1).upper()
        tag_value = match.group(2).strip()
        if tag_name not in tags:
            tags[tag_name] = tag_value

    result.thought = tags.get("THOUGHT", "")

    if "ANSWER" in tags:
        result.action_type = "answer"
        raw_answer = tags["ANSWER"].strip()
        for token in raw_answer.split():
            if len(token) == 1 and token.upper() in "ABCDEFGH":
                result.answer = token.upper()
                break
        if not result.answer:
            result.answer = raw_answer
        return result

    if "CALL" in tags:
        result.action_type = "call"
        result.tool_name = tags["CALL"].strip()
        raw_args = tags.get("ARGS", "{}")
        try:
            result.tool_args = json.loads(raw_args)
            if not isinstance(result.tool_args, dict):
                result.tool_args = {}
        except (json.JSONDecodeError, ValueError):
            result.tool_args = {"_raw": raw_args}
        return result

    if "NEXT" in tags:
        result.action_type = "next"
        result.next_phase = tags["NEXT"].strip().upper()
        return result

    result.action_type = "none"
    return result


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_MARKER) + 32:
        return text[:limit]
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _extract_semantic_failure(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, dict):
        error_value = value.get("error")
        if error_value:
            return str(error_value)

        returncode = value.get("returncode")
        if returncode not in (None, 0):
            return str(
                value.get("stderr")
                or value.get("stdout")
                or f"returncode={returncode}"
            )

        for key in ("stderr", "stdout", "observation", "raw_result"):
            nested = _extract_semantic_failure(value.get(key))
            if nested:
                return nested
        return ""

    if isinstance(value, (list, tuple)):
        for item in value:
            nested = _extract_semantic_failure(item)
            if nested:
                return nested
        return ""

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[:1] in {"{", "["}:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if parsed is not None:
                nested = _extract_semantic_failure(parsed)
                if nested:
                    return nested
        lower = text.lower()
        for marker in _SEMANTIC_FAILURE_MARKERS:
            if marker in lower:
                return text
        return ""

    return ""


def _tool_result_indexes(messages: list[LLMMessage]) -> list[int]:
    return [
        idx
        for idx, message in enumerate(messages)
        if idx >= 2 and message.role == "user" and message.content.startswith("Tool result")
    ]


def _fit_messages_to_budget(messages: list[LLMMessage], max_chars: int) -> list[LLMMessage]:
    if max_chars <= 0 or not messages:
        return messages
    total_chars = sum(len(message.content) for message in messages)
    if total_chars <= max_chars:
        return messages
    if len(messages) <= 2:
        return messages

    preserved = messages[:2]
    preserved_chars = sum(len(message.content) for message in preserved)
    if preserved_chars >= max_chars:
        remaining = max(max_chars - len(messages[0].content), 0)
        return [
            messages[0],
            LLMMessage(role=messages[1].role, content=_truncate_text(messages[1].content, remaining)),
        ]

    budget = max_chars - preserved_chars
    tail: list[LLMMessage] = []
    used = 0
    for message in reversed(messages[2:]):
        length = len(message.content)
        if used + length > budget:
            continue
        tail.append(message)
        used += length
    tail.reverse()
    return preserved + tail


def _prepare_messages_for_call(messages: list[LLMMessage], max_context_chars: int) -> list[LLMMessage]:
    if max_context_chars <= 0:
        return messages
    if len(messages) <= 2:
        return _fit_messages_to_budget(messages, max_context_chars)

    recent_tool_results = set(_tool_result_indexes(messages)[-_RECENT_TOOL_RESULTS_TO_KEEP:])
    prepared: list[LLMMessage] = []
    for idx, message in enumerate(messages):
        content = message.content
        if idx >= 2 and idx not in recent_tool_results:
            content = _truncate_text(content, _OLDER_MESSAGE_LIMIT)
        prepared.append(LLMMessage(role=message.role, content=content))
    prepared = _fit_messages_to_budget(prepared, max_context_chars)

    total_chars = sum(len(message.content) for message in prepared)
    if total_chars <= max_context_chars:
        return prepared

    tightened: list[LLMMessage] = []
    for idx, message in enumerate(prepared):
        content = message.content
        if idx >= 2:
            content = _truncate_text(content, _MIN_MESSAGE_LIMIT)
        tightened.append(LLMMessage(role=message.role, content=content))
    return _fit_messages_to_budget(tightened, max_context_chars)


def _format_allowed_next(allowed_next: list[str]) -> str:
    if not allowed_next:
        return "(none)"
    return ", ".join(allowed_next)


def _invalid_phase_transition_feedback(current_phase: str, allowed_next: list[str]) -> str:
    return (
        "Invalid phase transition.\n"
        f"Current phase: {current_phase}\n"
        f"Allowed next: {_format_allowed_next(allowed_next)}"
    )


def _unknown_phase_feedback(current_phase: str, allowed_next: list[str]) -> str:
    return (
        "Unknown phase.\n"
        f"Current phase: {current_phase}\n"
        f"Allowed next: {_format_allowed_next(allowed_next)}"
    )


def _invalid_answer_feedback(current_phase: str) -> str:
    return (
        "Invalid answer output.\n"
        f"Current phase: {current_phase}\n"
        "Answer is only allowed in CONCLUDE."
    )


class PhaseExecutorAgent:
    """ReAct executor that uses tag-based progressive disclosure with skill phases."""

    def __init__(
        self,
        llm_config: LLMConfig,
        prompt_root: Path,
        toolbox: Toolbox,
        *,
        max_context_chars: int = 0,
    ):
        self.llm = OpenAICompatibleLLM(llm_config)
        self.prompt_root = prompt_root
        self.toolbox = toolbox
        self.max_context_chars = max_context_chars

    def run(
        self,
        *,
        role_name: str,
        system_prompt: str,
        initial_user_prompt: str,
        phases: dict[str, SkillPhase],
        allowed_tools: list[str] | None,
        max_steps: int,
        log_dir: Path,
        phase_transition_graph: dict[str, list[str]],
        resolved_conclude_prompt: str,
        fallback_conclude_prompt: str,
    ) -> tuple[ParsedAction, str, list[ToolCallRecord], list[PhaseTransition]]:
        tool_protocol = load_prompt(self.prompt_root / "tool_agent_protocol.md")
        full_system = (
            system_prompt
            + "\n\n"
            + tool_protocol.format(tools_json=self.toolbox.tool_prompt(allowed_tools))
        )

        messages = [
            LLMMessage(role="system", content=full_system),
            LLMMessage(role="user", content=initial_user_prompt),
        ]
        raw_outputs: list[str] = []
        records: list[ToolCallRecord] = []
        transitions: list[PhaseTransition] = []
        current_phase = "INIT"
        final_action = ParsedAction(action_type="answer", answer="", thought="max steps reached")

        allowed_set = set(allowed_tools or [])
        recognized_phases = set(phase_transition_graph)

        for step_idx in range(1, max_steps + 1):
            remaining_steps = max_steps - step_idx + 1
            if current_phase != "CONCLUDE" and remaining_steps <= 3:
                raw_outputs.append("[runtime_feedback] [fallback conclude] remaining_steps<=3")
                transitions.append(
                    PhaseTransition(
                        step_index=step_idx,
                        from_phase=current_phase,
                        to_phase="CONCLUDE",
                        thought="[fallback conclude] remaining_steps<=3",
                    )
                )
                current_phase = "CONCLUDE"
                messages.append(
                    LLMMessage(
                        role="user",
                        content=fallback_conclude_prompt,
                    )
                )

            result = self.llm.chat(
                _prepare_messages_for_call(messages, self.max_context_chars)
            )
            log_llm_call(
                log_dir / f"{role_name}_steps",
                f"{role_name}_step_{step_idx}",
                result,
            )
            raw_outputs.append(result.text)
            messages.append(LLMMessage(role="assistant", content=result.text))

            parsed = parse_executor_tags(result.text)

            if parsed.action_type == "answer":
                if current_phase == "CONCLUDE":
                    final_action = parsed
                    break
                feedback = _invalid_answer_feedback(current_phase)
                raw_outputs.append(f"[runtime_feedback]\n{feedback}")
                messages.append(
                    LLMMessage(
                        role="user",
                        content=feedback,
                    )
                )
                continue

            if parsed.action_type == "call":
                tool_name = parsed.tool_name
                arguments = parsed.tool_args or {}
                thought = parsed.thought
                try:
                    if allowed_set and tool_name not in allowed_set:
                        raise PermissionError(
                            f"Tool `{tool_name}` is not in allowed-tools for this skill."
                        )
                    raw_result = self.toolbox.execute(tool_name, arguments)
                    semantic_failure = _extract_semantic_failure(raw_result)
                    if semantic_failure:
                        success = False
                        error = semantic_failure
                    else:
                        success = True
                        error = ""
                    observation = json.dumps(raw_result, ensure_ascii=False, default=str)
                except Exception as exc:
                    raw_result = {"error": str(exc)}
                    observation = json.dumps(raw_result, ensure_ascii=False)
                    success = False
                    error = str(exc)

                records.append(
                    ToolCallRecord(
                        step_index=step_idx,
                        thought=thought,
                        tool_name=tool_name,
                        arguments=arguments,
                        observation=observation,
                        success=success,
                        raw_result=raw_result,
                        error=error,
                    )
                )
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            f"Tool result for step {step_idx}:\n"
                            f"- tool: {tool_name}\n"
                            f"- success: {success}\n"
                            f"- observation: {observation}\n\n"
                            f"You are in Phase: {current_phase}. "
                            "Continue following the current phase instructions."
                        ),
                    )
                )

            elif parsed.action_type == "next":
                next_phase = parsed.next_phase
                allowed_next = phase_transition_graph.get(current_phase, [])
                if next_phase not in recognized_phases:
                    feedback = _unknown_phase_feedback(current_phase, allowed_next)
                    raw_outputs.append(f"[runtime_feedback]\n{feedback}")
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=feedback,
                        )
                    )
                elif next_phase not in allowed_next:
                    feedback = _invalid_phase_transition_feedback(current_phase, allowed_next)
                    raw_outputs.append(f"[runtime_feedback]\n{feedback}")
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=feedback,
                        )
                    )
                elif next_phase in phases:
                    transition_thought = parsed.thought
                    if next_phase == "CONCLUDE":
                        transition_thought = (
                            f"[resolved conclude] {parsed.thought}".strip()
                            if parsed.thought else "[resolved conclude]"
                        )
                    transitions.append(
                        PhaseTransition(
                            step_index=step_idx,
                            from_phase=current_phase,
                            to_phase=next_phase,
                            thought=transition_thought,
                        )
                    )
                    current_phase = next_phase
                    phase_content = (
                        resolved_conclude_prompt
                        if next_phase == "CONCLUDE"
                        else (
                            f"=== Phase: {next_phase} ===\n\n"
                            f"{phases[next_phase].content}\n\n"
                            "Follow the instructions above for this phase."
                        )
                    )
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=phase_content,
                        )
                    )
                else:
                    feedback = _unknown_phase_feedback(current_phase, allowed_next)
                    raw_outputs.append(f"[runtime_feedback]\n{feedback}")
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=feedback,
                        )
                    )

            else:
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Your response did not contain a recognized action tag. "
                            "You must use exactly one of:\n"
                            "- <CALL>tool_name</CALL><ARGS>{...}</ARGS> to call a tool\n"
                            "- <NEXT>PHASE_NAME</NEXT> to transition to the next phase\n"
                            "- <ANSWER>A/B/C/D</ANSWER> to give your final answer\n\n"
                            f"Current phase: {current_phase}. Try again."
                        ),
                    )
                )

        return final_action, "\n\n".join(raw_outputs), records, transitions
