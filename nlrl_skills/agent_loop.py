from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from .config import LLMConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import load_prompt
from .schemas import LLMMessage, ToolCallRecord
from .tools import Toolbox

_TRUNCATION_MARKER = "\n\n[truncated]\n"
_TOKENIZER_CACHE: dict[str, object] = {}
_TOKENIZER_LOCK = Lock()


def _load_tokenizer(tokenizer_path: str):
    with _TOKENIZER_LOCK:
        cached = _TOKENIZER_CACHE.get(tokenizer_path)
        if cached is not None:
            return cached
        from transformers.models.auto.tokenization_auto import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_path,
            local_files_only=True,
            trust_remote_code=True,
        )
        _TOKENIZER_CACHE[tokenizer_path] = tokenizer
        return tokenizer


def _render_messages_for_qwen(
    messages: list[LLMMessage],
    *,
    add_generation_prompt: bool = True,
    enable_thinking: bool | None = None,
) -> str:
    parts: list[str] = []
    start_index = 0
    if messages and messages[0].role == "system":
        parts.append(f"<|im_start|>system\n{messages[0].content}<|im_end|>\n")
        start_index = 1
    for message in messages[start_index:]:
        parts.append(f"<|im_start|>{message.role}\n{message.content}<|im_end|>\n")
    if add_generation_prompt:
        parts.append("<|im_start|>assistant\n")
        if enable_thinking is False:
            parts.append("<think>\n\n</think>\n\n")
    return "".join(parts)


def _count_prompt_tokens(
    messages: list[LLMMessage],
    *,
    tokenizer_path: str,
    enable_thinking: bool | None,
) -> int:
    tokenizer = _load_tokenizer(tokenizer_path)
    rendered = _render_messages_for_qwen(
        messages,
        add_generation_prompt=True,
        enable_thinking=enable_thinking,
    )
    return len(tokenizer.encode(rendered, add_special_tokens=False))


def _truncate_text_to_token_budget(text: str, limit_tokens: int, *, tokenizer_path: str) -> str:
    if limit_tokens <= 0:
        return ""
    tokenizer = _load_tokenizer(tokenizer_path)
    token_ids = tokenizer.encode(text, add_special_tokens=False)
    if len(token_ids) <= limit_tokens:
        return text
    marker_ids = tokenizer.encode(_TRUNCATION_MARKER, add_special_tokens=False)
    if limit_tokens <= len(marker_ids) + 8:
        return tokenizer.decode(token_ids[:limit_tokens], skip_special_tokens=False).rstrip()
    kept_ids = token_ids[: limit_tokens - len(marker_ids)]
    return tokenizer.decode(kept_ids, skip_special_tokens=False).rstrip() + _TRUNCATION_MARKER


def _history_turns(messages: list[LLMMessage]) -> list[list[LLMMessage]]:
    turns: list[list[LLMMessage]] = []
    idx = 0
    while idx < len(messages):
        current = messages[idx]
        if (
            current.role == "assistant"
            and idx + 1 < len(messages)
            and messages[idx + 1].role == "user"
        ):
            turns.append([current, messages[idx + 1]])
            idx += 2
            continue
        turns.append([current])
        idx += 1
    return turns


def _flatten_turns(turns: list[list[LLMMessage]]) -> list[LLMMessage]:
    flattened: list[LLMMessage] = []
    for turn in turns:
        flattened.extend(turn)
    return flattened


def _truncate_turn_to_fit(
    base_messages: list[LLMMessage],
    turn: list[LLMMessage],
    *,
    max_input_tokens: int,
    tokenizer_path: str,
    enable_thinking: bool | None,
) -> list[LLMMessage] | None:
    working = [LLMMessage(role=message.role, content=message.content) for message in turn]
    if (
        _count_prompt_tokens(
            base_messages + working,
            tokenizer_path=tokenizer_path,
            enable_thinking=enable_thinking,
        )
        <= max_input_tokens
    ):
        return working

    tokenizer = _load_tokenizer(tokenizer_path)
    truncate_order = sorted(
        range(len(working)),
        key=lambda idx: (working[idx].role != "user", -idx),
    )
    for target_idx in truncate_order:
        source_text = working[target_idx].content
        if not source_text:
            continue
        token_count = len(tokenizer.encode(source_text, add_special_tokens=False))
        low = 0
        high = token_count
        best_text: str | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate_turn = [
                LLMMessage(role=message.role, content=message.content)
                for message in working
            ]
            candidate_turn[target_idx] = LLMMessage(
                role=working[target_idx].role,
                content=_truncate_text_to_token_budget(
                    source_text,
                    mid,
                    tokenizer_path=tokenizer_path,
                ),
            )
            if (
                _count_prompt_tokens(
                    base_messages + candidate_turn,
                    tokenizer_path=tokenizer_path,
                    enable_thinking=enable_thinking,
                )
                <= max_input_tokens
            ):
                best_text = candidate_turn[target_idx].content
                low = mid + 1
            else:
                high = mid - 1
        if best_text is None:
            continue
        working[target_idx] = LLMMessage(role=working[target_idx].role, content=best_text)
        if (
            _count_prompt_tokens(
                base_messages + working,
                tokenizer_path=tokenizer_path,
                enable_thinking=enable_thinking,
            )
            <= max_input_tokens
        ):
            return working
    return None


def _prepare_messages_for_call(
    messages: list[LLMMessage],
    *,
    max_input_tokens: int,
    tokenizer_path: str,
    enable_thinking: bool | None,
) -> list[LLMMessage]:
    if max_input_tokens <= 0 or not messages:
        return messages

    prepared = [LLMMessage(role=message.role, content=message.content) for message in messages]
    if (
        _count_prompt_tokens(
            prepared,
            tokenizer_path=tokenizer_path,
            enable_thinking=enable_thinking,
        )
        <= max_input_tokens
    ):
        return prepared

    preserved = prepared[:2]
    if (
        _count_prompt_tokens(
            preserved,
            tokenizer_path=tokenizer_path,
            enable_thinking=enable_thinking,
        )
        > max_input_tokens
    ):
        tightened = _truncate_turn_to_fit(
            [],
            preserved,
            max_input_tokens=max_input_tokens,
            tokenizer_path=tokenizer_path,
            enable_thinking=enable_thinking,
        )
        if tightened is not None:
            return tightened
        return preserved[:1]

    kept_turns_reversed: list[list[LLMMessage]] = []
    for turn in reversed(_history_turns(prepared[2:])):
        candidate_turns = [turn, *kept_turns_reversed]
        candidate_messages = preserved + _flatten_turns(list(reversed(candidate_turns)))
        if (
            _count_prompt_tokens(
                candidate_messages,
                tokenizer_path=tokenizer_path,
                enable_thinking=enable_thinking,
            )
            <= max_input_tokens
        ):
            kept_turns_reversed.append(turn)
            continue
        base_messages = preserved + _flatten_turns(list(reversed(kept_turns_reversed)))
        tightened_turn = _truncate_turn_to_fit(
            base_messages,
            turn,
            max_input_tokens=max_input_tokens,
            tokenizer_path=tokenizer_path,
            enable_thinking=enable_thinking,
        )
        if tightened_turn is not None:
            kept_turns_reversed.append(tightened_turn)
        break
    return preserved + _flatten_turns(list(reversed(kept_turns_reversed)))


class JSONToolAgent:
    def __init__(
        self,
        llm_config: LLMConfig,
        prompt_root: Path,
        toolbox: Toolbox,
        *,
        executor_total_token_budget: int = 32768,
        executor_tokenizer_path: str = "/data/xsy/codes/checkpoints/Qwen3-8B",
    ):
        self.llm = OpenAICompatibleLLM(llm_config)
        self.prompt_root = prompt_root
        self.toolbox = toolbox
        self.executor_total_token_budget = executor_total_token_budget
        self.executor_tokenizer_path = executor_tokenizer_path

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
            max_output_tokens = self.llm.resolve_max_tokens()
            payload, result = self.llm.chat_json(
                _prepare_messages_for_call(
                    messages,
                    max_input_tokens=max(self.executor_total_token_budget - (max_output_tokens or 0), 0),
                    tokenizer_path=self.executor_tokenizer_path,
                    enable_thinking=self.llm.config.enable_thinking,
                )
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
                raw_result: dict | str | list | int | float | bool | None
                repeated_success = (
                    records
                    and records[-1].tool_name == tool_name
                    and records[-1].arguments == arguments
                    and records[-1].success
                )
                repeated_failed_twice = (
                    len(records) >= 2
                    and records[-1].tool_name == tool_name
                    and records[-1].arguments == arguments
                    and not records[-1].success
                    and records[-2].tool_name == tool_name
                    and records[-2].arguments == arguments
                    and not records[-2].success
                )
                if repeated_success:
                    raw_result = {
                        "error": (
                            f"Repeated identical tool call blocked for `{tool_name}`. "
                            "Reuse the prior successful observation or choose a different next step."
                        )
                    }
                    success = False
                    error = str(raw_result["error"])
                elif repeated_failed_twice:
                    raw_result = {
                        "error": (
                            f"Repeated identical failing tool call blocked for `{tool_name}`. "
                            "The same arguments already failed twice; change strategy instead of retrying unchanged."
                        )
                    }
                    success = False
                    error = str(raw_result["error"])
                else:
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
