"""
Direct single-executor alternative for the built-in 6-skill skill_eval flow.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .benchmark_loader import load_benchmark
from .config import (
    DEFAULT_TEMP_DIR,
    DIRECT_EXECUTOR_MAX_STEPS,
    DIRECT_EXECUTOR_MAX_TOKENS,
    DIRECT_EXECUTOR_MODEL_NAME,
    MAX_CONTEXT_CHARS,
)
from .evaluator import evaluate_execution, safe_choice_fallback
from .network_errors import NetworkCallError
from .runtime import ToolRuntime, summarize_result
from .schemas import BenchmarkItem, ExecutedSkillStep, SkillExecutionRecord
from .skill_llm_planner import _skill_guidance_lines
from .skill_planner_client import chat_completion
from .skill_router import SkillSpec, route_skill
from .tool_catalog import ToolMeta, build_catalog
from .tool_router import infer_question_profile, shortlist_tools

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_PROTOCOL_PATH = PROJECT_ROOT / "prompts" / "tool_agent_protocol.md"

DIRECT_EXECUTOR_SYSTEM_PROMPT = """
You are a benchmark-faithful Earth observation executor.
You are already given a routed skill family and a benchmark-aware tool shortlist.
Use the routed skill as a strong prior, but decide one next tool at a time from the shortlisted tools.
Do not output a full tool sequence upfront.
When enough evidence has been collected, stop and return the final answer choice.
""".strip()


def _truncate_text(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 200] + "\n\n[truncated]\n"


def _serialize(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _tool_protocol_entries(shortlisted: list[ToolMeta]) -> str:
    entries = []
    for tool in shortlisted:
        entries.append(
            json.dumps(
                {
                    "name": tool.canonical_name,
                    "description": tool.short_description(220),
                    "parameters": tool.parameters,
                    "source": tool.toolkit,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(entries)


def _build_system_prompt(shortlisted: list[ToolMeta]) -> str:
    protocol = TOOL_PROTOCOL_PATH.read_text(encoding="utf-8")
    return DIRECT_EXECUTOR_SYSTEM_PROMPT + "\n\n" + protocol.format(
        tools_json=_tool_protocol_entries(shortlisted)
    )


def _file_preview(file_list: list[str], limit: int = 40) -> str:
    preview = file_list[:limit]
    remaining = max(0, len(file_list) - len(preview))
    lines = "\n".join(f"- {name}" for name in preview) or "- (none)"
    if remaining:
        lines += f"\n- ... ({remaining} more files omitted for brevity)"
    return lines


def _choice_preview(choices: list[str]) -> str:
    return "\n".join(
        f"{chr(ord('A') + idx)}. {choice}"
        for idx, choice in enumerate(choices[:4])
    )


def _build_user_prompt(
    item: BenchmarkItem,
    *,
    skill: SkillSpec,
    shortlisted: list[ToolMeta],
    focus_tools: list[str],
    guidance_lines: list[str],
) -> str:
    blocks = [
        "Question:\n" + item.question_text.strip(),
        "Data directory:\n" + item.data_dir,
        "Available files preview:\n" + _file_preview(item.file_list),
        "Choices:\n" + _choice_preview([str(choice) for choice in item.choices]),
        (
            "Routed skill family:\n"
            f"- skill_id: {skill.skill_id}\n"
            f"- display_name: {skill.display_name}\n"
            f"- description: {skill.description}"
        ),
        "Skill-prior focus tools inside the current shortlist:\n- "
        + (", ".join(focus_tools) if focus_tools else "(none)"),
        "Skill guidance:\n" + "\n".join(f"- {line}" for line in guidance_lines),
        (
            "Execution requirements:\n"
            "- You may use any exact tool name from the shortlisted tools shown in the system prompt.\n"
            "- Do not invent tools or unseen file paths.\n"
            "- Prefer actual computation and file inspection over guessing.\n"
            "- Decide one next tool at a time. Do not emit a full tool list upfront.\n"
            "- When you can answer the multiple-choice question, return a final JSON object with a valid choice_label."
        ),
    ]
    return "\n\n".join(block.strip() for block in blocks if block.strip())


def _append_log(record: SkillExecutionRecord, stage: str, message: str, **extra: Any) -> None:
    record.logs.append(
        {
            "stage": stage,
            "message": message,
            "extra": extra or {},
        }
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response; expected JSON object.")
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Top-level JSON must be an object.")
        return payload
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Unable to locate JSON object in response: {text[:400]}")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("Top-level JSON must be an object.")
        return payload


def _tool_result_feedback(*, step_index: int, tool_name: str, success: bool, observation: str) -> str:
    return (
        f"Tool result for step {step_index}:\n"
        f"- tool_name: {tool_name}\n"
        f"- success: {success}\n"
        f"- observation: {_truncate_text(observation, 6000)}\n"
        "Decide the next step using the same JSON protocol. "
        "If the evidence is already sufficient, return a final JSON object."
    )


def _normalize_choice_label(payload: dict[str, Any], *, choice_count: int) -> str:
    choice_label = str(payload.get("choice_label", "")).strip().upper()
    if choice_label in {"A", "B", "C", "D"} and ord(choice_label) - ord("A") < choice_count:
        return choice_label
    choice_index = payload.get("choice_index")
    if isinstance(choice_index, int) and 1 <= choice_index <= choice_count:
        return chr(ord("A") + choice_index - 1)
    return ""


def _resolve_final_choice(
    payload: dict[str, Any] | None,
    *,
    item: BenchmarkItem,
) -> tuple[int, str, str, str, bool]:
    if payload is None:
        idx, label, text = safe_choice_fallback([str(choice) for choice in item.choices], item.question_id)
        return (
            idx,
            label,
            text,
            "Fallback choice used because direct executor stopped without returning a final answer.",
            True,
        )
    label = _normalize_choice_label(payload, choice_count=len(item.choices))
    summary = str(payload.get("summary", payload.get("thought", ""))).strip()
    if label:
        idx = ord(label) - ord("A") + 1
        text = str(item.choices[idx - 1])
        return idx, label, text, summary, False
    idx, label, text = safe_choice_fallback([str(choice) for choice in item.choices], item.question_id)
    reason = "Fallback choice used because direct executor returned no valid choice label."
    if summary:
        reason += f" Summary: {summary}"
    return idx, label, text, reason, True


def _execution_summary(record: SkillExecutionRecord) -> str:
    lines = []
    for step in record.executed_steps:
        prefix = f"{step.step_index + 1}. {step.chosen_tool_name}"
        if step.success:
            lines.append(
                f"{prefix} args={json.dumps(step.arguments, ensure_ascii=False)} -> {step.result_summary}"
            )
        else:
            lines.append(
                f"{prefix} FAILED args={json.dumps(step.arguments, ensure_ascii=False)} -> {step.error}"
            )
    return "\n".join(lines)


def _persist_outputs(record: SkillExecutionRecord, llm_trace: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    question_dir = output_dir / f"question_{record.question_id}"
    question_dir.mkdir(parents=True, exist_ok=True)
    (question_dir / "execution_record.json").write_text(
        json.dumps(asdict(record), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (question_dir / "execution_log.json").write_text(
        json.dumps(record.logs, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (question_dir / "llm_trace.json").write_text(
        json.dumps(llm_trace, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def execute_one_question(question_id: str, output_dir: Path) -> SkillExecutionRecord:
    item = load_benchmark(question_ids=[str(question_id)])[0]
    catalog = build_catalog()
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(catalog, item.question_text, item.file_list, question_id=item.question_id)
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    allowed_names = {tool.canonical_name for tool in shortlisted}
    focus_tools = [
        tool.canonical_name
        for tool in shortlisted
        if tool.canonical_name in set(skill.tool_allowlist)
    ]
    guidance_lines = _skill_guidance_lines(item, skill, profile, focus_tools)
    system_prompt = _build_system_prompt(shortlisted)
    user_prompt = _build_user_prompt(
        item,
        skill=skill,
        shortlisted=shortlisted,
        focus_tools=focus_tools,
        guidance_lines=guidance_lines,
    )

    record = SkillExecutionRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=skill.skill_id,
        planning_source="skill-direct-executor",
        planned_tool_sequence=[],
    )
    llm_trace: dict[str, Any] = {
        "planner": {},
        "parameter_steps": [],
        "answer_selector": {},
        "direct_executor": {
            "skill_id": skill.skill_id,
            "focus_tools": focus_tools,
            "shortlisted_tool_names": [tool.canonical_name for tool in shortlisted],
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "steps": [],
            "final_payload": {},
        },
    }
    _append_log(
        record,
        "direct_executor_init",
        "Initialized direct-executor skill run.",
        skill_id=skill.skill_id,
        shortlisted_tool_count=len(shortlisted),
        focus_tools=focus_tools,
    )
    log.info(
        "Q%s | direct-executor start | skill=%s shortlist=%d",
        item.question_id,
        skill.skill_id,
        len(shortlisted),
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    runtime = ToolRuntime(DEFAULT_TEMP_DIR / f"q{item.question_id}")
    final_payload: dict[str, Any] | None = None
    network_error_message = ""

    for step_index in range(1, DIRECT_EXECUTOR_MAX_STEPS + 1):
        try:
            raw = chat_completion(
                messages,
                temperature=0.0,
                max_tokens=DIRECT_EXECUTOR_MAX_TOKENS,
                model=DIRECT_EXECUTOR_MODEL_NAME,
            )
        except NetworkCallError as exc:
            network_error_message = str(exc)
            _append_log(
                record,
                "network_error",
                "Network error during direct executor generation. Skipping this question.",
                error=str(exc),
                network_error=True,
            )
            llm_trace["direct_executor"]["network_error"] = {
                "stage": "direct-executor",
                "message": str(exc),
            }
            break

        trace_step = {
            "step_index": step_index,
            "raw_output": raw,
        }
        messages.append({"role": "assistant", "content": raw})

        try:
            payload = _extract_json_object(raw)
        except Exception as exc:
            trace_step["parse_error"] = str(exc)
            llm_trace["direct_executor"]["steps"].append(trace_step)
            _append_log(
                record,
                "protocol_error",
                "Direct executor returned invalid JSON and the run stopped.",
                error=str(exc),
                raw_output=raw,
            )
            break

        action = str(payload.get("action", "")).strip().lower()
        trace_step["action"] = action
        llm_trace["direct_executor"]["steps"].append(trace_step)

        if action == "final":
            final_payload = payload
            llm_trace["direct_executor"]["final_payload"] = payload
            _append_log(
                record,
                "direct_executor_final",
                "Direct executor returned a final answer payload.",
                payload=payload,
            )
            break

        if action != "tool":
            _append_log(
                record,
                "protocol_error",
                "Direct executor returned an unsupported action and the run stopped.",
                raw_output=raw,
                action=action,
            )
            break

        tool_name = str(payload.get("tool_name", "")).strip()
        thought = str(payload.get("thought", "")).strip()
        raw_arguments = payload.get("arguments", {})
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}

        if not isinstance(raw_arguments, dict):
            raw_result = {"error": "Tool arguments must be a JSON object."}
            success = False
            error = str(raw_result["error"])
        elif tool_name not in allowed_names:
            raw_result = {"error": f"Tool `{tool_name}` is not in the shortlisted tool list."}
            success = False
            error = str(raw_result["error"])
        else:
            try:
                raw_result = runtime.execute(tool_name, arguments)
                success = True
                error = ""
            except Exception as exc:
                raw_result = {"error": str(exc)}
                success = False
                error = str(exc)

        observation = _serialize(raw_result)
        result_summary = summarize_result(raw_result)
        record.executed_steps.append(
            ExecutedSkillStep(
                step_index=step_index - 1,
                planned_tool_name=tool_name,
                chosen_tool_name=tool_name,
                arguments=arguments,
                raw_result=raw_result,
                result_summary=result_summary if success else "",
                success=success,
                error=error,
                worker_rationale=thought,
            )
        )
        if success:
            _append_log(
                record,
                "tool_success",
                f"Executed {tool_name}.",
                arguments=arguments,
                result_summary=result_summary,
            )
        else:
            _append_log(
                record,
                "tool_error",
                f"Execution failed for {tool_name}.",
                arguments=arguments,
                error=error,
            )
        messages.append(
            {
                "role": "user",
                "content": _tool_result_feedback(
                    step_index=step_index,
                    tool_name=tool_name,
                    success=success,
                    observation=observation,
                ),
            }
        )

    if network_error_message:
        record.metrics = {
            "network_error": True,
            "skipped": True,
            "predicted_count": len(record.executed_steps),
            "gold_count": len(item.gold_tool_calls),
        }
        _append_log(
            record,
            "skip",
            "Skipped question after direct executor network error.",
            error=network_error_message,
        )
    else:
        idx, label, text, reason, fallback_used = _resolve_final_choice(
            final_payload,
            item=item,
        )
        record.final_choice_index = idx
        record.final_choice_label = label
        record.final_choice_text = text
        record.final_choice_reason = reason
        record.final_answer_fallback_used = fallback_used
        record.metrics = evaluate_execution(record, item.gold_tool_calls, item.gt_answer)
        _append_log(
            record,
            "answer_selection",
            "Resolved final answer for direct executor run.",
            choice_index=idx,
            choice_label=label,
            fallback=fallback_used,
            reason=reason,
            execution_summary=_execution_summary(record),
        )
        _append_log(record, "evaluation", "Computed benchmark metrics.", metrics=record.metrics)

    _persist_outputs(record, llm_trace, output_dir)
    log.info(
        "Q%s | direct-executor done | skill=%s answer=%s fallback=%s TAO=%.4f TIO=%.4f TEM=%.4f ACC=%.4f",
        item.question_id,
        skill.skill_id,
        record.final_choice_label or "?",
        record.final_answer_fallback_used,
        record.metrics.get("tool_any_order", 0.0),
        record.metrics.get("tool_in_order", 0.0),
        record.metrics.get("tool_exact_match", 0.0),
        record.metrics.get("accuracy", 0.0),
    )
    return record
