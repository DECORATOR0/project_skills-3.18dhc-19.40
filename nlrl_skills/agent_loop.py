from __future__ import annotations

import json
from pathlib import Path

from .config import LLMConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import load_prompt
from .schemas import LLMMessage, ToolCallRecord
from .tools import Toolbox
from .utils import extract_json_object


class JSONToolAgent:
    def __init__(self, llm_config: LLMConfig, prompt_root: Path, toolbox: Toolbox):
        self.llm = OpenAICompatibleLLM(llm_config)
        self.prompt_root = prompt_root
        self.toolbox = toolbox

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
            result = self.llm.chat(messages)
            log_llm_call(log_dir / f"{role_name}_steps", f"{role_name}_step_{step_idx}", result)
            raw_outputs.append(result.text)
            payload = extract_json_object(result.text)
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
