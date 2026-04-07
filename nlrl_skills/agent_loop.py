from __future__ import annotations

import json
from pathlib import Path

from .config import LLMConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import load_prompt
from .schemas import LLMMessage, ToolCallRecord
from .tools import Toolbox

_TRUNCATION_MARKER = "\n\n[truncated]\n"
_OLDER_MESSAGE_LIMIT = 1600
_MIN_MESSAGE_LIMIT = 600
_RECENT_TOOL_RESULTS_TO_KEEP = 3
_EARLY_TOOL_RESULTS_TO_KEEP = 2


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_MARKER) + 32:
        return text[:limit]
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _tool_result_indexes(messages: list[LLMMessage]) -> list[int]:
    return [
        idx
        for idx, message in enumerate(messages)
        if idx >= 2 and message.role == "user" and message.content.startswith("Tool result for step ")
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

    tool_result_indexes = _tool_result_indexes(messages)
    recent_tool_results = set(tool_result_indexes[-_RECENT_TOOL_RESULTS_TO_KEEP:])
    if tool_result_indexes:
        # Keep the first tool result as a stable evidence anchor. In multi-block
        # EO tasks this is often the only full discovery listing available for
        # later windows/groups after several downstream tool turns.
        recent_tool_results.update(tool_result_indexes[:_EARLY_TOOL_RESULTS_TO_KEEP])
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


class JSONToolAgent:
    def __init__(self, llm_config: LLMConfig, prompt_root: Path, toolbox: Toolbox, *, max_context_chars: int = 16384):
        self.llm = OpenAICompatibleLLM(llm_config)
        self.prompt_root = prompt_root
        self.toolbox = toolbox
        self.max_context_chars = max_context_chars

    def run(
        self,
        *,
        role_name: str,
        base_system_prompt: str,
        user_prompt: str,
        allowed_tools: list[str] | None,
        max_steps: int,
        log_dir: Path,
    ) -> tuple[dict, str, list[ToolCallRecord]]:
        tool_protocol = load_prompt(self.prompt_root / "tool_agent_protocol.md")
        messages = [
            LLMMessage(
                role="system",
                content=base_system_prompt
                + "\n\n"
                + tool_protocol.format(tools_json=self.toolbox.tool_prompt(allowed_tools)),
            ),
            LLMMessage(role="user", content=user_prompt),
        ]
        raw_outputs: list[str] = []
        records: list[ToolCallRecord] = []
        final_payload: dict = {
            "action": "final",
            "final_answer": "",
            "choice_label": "",
            "summary": "",
        }
        for step_idx in range(1, max_steps + 1):
            payload, result = self.llm.chat_json(
                _prepare_messages_for_call(messages, self.max_context_chars)
            )
            log_llm_call(log_dir / f"{role_name}_steps", f"{role_name}_step_{step_idx}", result)
            raw_outputs.append(result.text)
            messages.append(LLMMessage(role="assistant", content=result.text))
            action = str(payload.get("action", "")).strip().lower()
            if action == "final":
                final_payload = payload
                break
            if action != "tool":
                raise ValueError(f"Unsupported action from {role_name}: {action}")
            tool_name = str(payload.get("tool_name", "")).strip()
            arguments = payload.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            thought = str(payload.get("thought", ""))
            allowed_set = set(allowed_tools or [])
            try:
                if allowed_set and tool_name not in allowed_set:
                    raise PermissionError(f"Tool `{tool_name}` is not allowed by the active skill.")
                raw_result = self.toolbox.execute(tool_name, arguments)
                if isinstance(raw_result, dict) and raw_result.get("returncode") not in (None, 0):
                    success = False
                    error = str(raw_result.get("stderr") or raw_result.get("stdout") or f"returncode={raw_result.get('returncode')}")
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
                        f"- tool_name: {tool_name}\n"
                        f"- success: {success}\n"
                        f"- observation: {observation}\n"
                        "Decide the next step using the same JSON protocol."
                    ),
                )
            )
        return final_payload, "\n\n".join(raw_outputs), records
