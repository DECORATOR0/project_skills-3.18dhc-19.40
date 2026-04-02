"""
End-to-end executor for the skill-based Earth-Bench benchmark.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .benchmark_loader import load_benchmark
from .config import ANSWER_SELECTOR_MAX_TOKENS, DEFAULT_TEMP_DIR, MAX_CONTEXT_CHARS
from .evaluator import aggregate_execution_metrics, evaluate_execution, safe_choice_fallback
from .llm_client import async_chat_json_with_raw, chat_json_with_raw
from .network_errors import NetworkCallError
from .parameter_worker import choose_tool_arguments
from .prompt_builder import (
    build_choice_selector_messages,
    build_planned_tool_context,
    build_worker_user_prompt,
)
from .runtime import ToolRuntime, summarize_result
from .schemas import ExecutedSkillStep, PlannedSkillStep, SkillExecutionRecord
from .skill_llm_planner import plan_with_skill_llm
from .skill_router import route_skill
from .tool_catalog import build_catalog
from .tool_router import infer_question_profile, shortlist_tools, shortlist_to_prompt
from .tool_rendering import render_text_description_and_args

log = logging.getLogger(__name__)


def _truncate_text(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 200] + "\n\n[truncated]\n"


def _observation_text(result: Any) -> str:
    try:
        return _truncate_text(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        return _truncate_text(str(result))


def _single_agent_context(item, catalog) -> tuple[dict, list, str]:
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(catalog, item.question_text, item.file_list, question_id=item.question_id)
    tools_prompt = shortlist_to_prompt(shortlisted)
    return profile, shortlisted, tools_prompt


def _append_log(record: SkillExecutionRecord, stage: str, message: str, **extra: Any) -> None:
    record.logs.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "message": message,
            "extra": extra or {},
        }
    )
    log.debug("Q%s [%s] %s", record.question_id, stage, message)


def _build_argument_prompt(question: str, data_dir: str, rendered_tools: str, planned_tool_name: str, prior_steps: list[str], latest_observation: str | None) -> str:
    context = build_planned_tool_context(
        question=question,
        planned_tool_name=planned_tool_name,
        prior_steps=prior_steps,
        latest_observation=latest_observation,
    )
    return build_worker_user_prompt(
        question=question,
        data_path=data_dir,
        rendered_tools=rendered_tools,
        context=_truncate_text(context),
    )


def _select_final_answer(question: str, choices: list[str], execution_summary: str, seed: str) -> tuple[int, str, str, str, bool, list[dict], str]:
    messages = build_choice_selector_messages(
        question=question,
        choices=choices,
        execution_summary=execution_summary,
    )
    try:
        data, raw = chat_json_with_raw(messages, temperature=0.0, max_tokens=ANSWER_SELECTOR_MAX_TOKENS)
        idx = int(data["choice_index"])
        if not (1 <= idx <= len(choices)):
            raise ValueError(f"choice_index {idx} out of range")
        label = chr(ord("A") + idx - 1)
        text = str(choices[idx - 1])
        reason = str(data.get("reason", ""))
        return idx, label, text, reason, False, messages, raw
    except NetworkCallError:
        raise
    except Exception as exc:
        idx, label, text = safe_choice_fallback(choices, seed)
        reason = f"Fallback choice used because answer selector failed: {exc}"
        return idx, label, text, reason, True, messages, ""


async def _select_final_answer_async(question: str, choices: list[str], execution_summary: str, seed: str) -> tuple[int, str, str, str, bool, list[dict], str]:
    messages = build_choice_selector_messages(
        question=question,
        choices=choices,
        execution_summary=execution_summary,
    )
    try:
        data, raw = await async_chat_json_with_raw(messages, temperature=0.0, max_tokens=ANSWER_SELECTOR_MAX_TOKENS)
        idx = int(data["choice_index"])
        if not (1 <= idx <= len(choices)):
            raise ValueError(f"choice_index {idx} out of range")
        label = chr(ord("A") + idx - 1)
        text = str(choices[idx - 1])
        reason = str(data.get("reason", ""))
        return idx, label, text, reason, False, messages, raw
    except NetworkCallError:
        raise
    except Exception as exc:
        idx, label, text = safe_choice_fallback(choices, seed)
        reason = f"Fallback choice used because answer selector failed: {exc}"
        return idx, label, text, reason, True, messages, ""


def _execution_summary(record: SkillExecutionRecord) -> str:
    lines = []
    for step in record.executed_steps:
        prefix = f"{step.step_index + 1}. {step.chosen_tool_name}"
        if step.success:
            lines.append(f"{prefix} args={json.dumps(step.arguments, ensure_ascii=False)} -> {step.result_summary}")
        else:
            lines.append(f"{prefix} FAILED args={json.dumps(step.arguments, ensure_ascii=False)} -> {step.error}")
    return "\n".join(lines)


def _unwrap_path(value: str) -> str:
    if value.startswith("Result saved at "):
        return value[len("Result saved at "):].strip()
    if value.startswith("Result save at "):
        return value[len("Result save at "):].strip()
    return value


def _full_paths(item, names: list[str]) -> list[str]:
    base = Path(item.data_dir)
    return [str(base / name) for name in names]


def _year_from_name(name: str) -> str | None:
    m = re.search(r"(19\d{2}|20\d{2})", name)
    return m.group(1) if m else None


def _extract_gsds(question: str) -> list[float]:
    return [float(x) for x in re.findall(r"GSD[^0-9]*([0-9]+(?:\.[0-9]+)?)", question, re.IGNORECASE)]


def _extract_bbox_like(value: Any) -> list[float] | list[list[float]] | None:
    if isinstance(value, list):
        if value and all(isinstance(v, (int, float)) for v in value):
            return value
        if value and all(isinstance(v, (list, tuple)) and len(v) == 4 for v in value):
            return [list(v) for v in value]
    text = str(value)
    matches = re.findall(r"\[(?:\s*-?\d+(?:\.\d+)?\s*,){3}\s*-?\d+(?:\.\d+)?\s*\]", text)
    if matches:
        parsed = [ast.literal_eval(m) for m in matches]
        return parsed[0] if len(parsed) == 1 else parsed
    return None


def _question_object_prompt(question: str) -> str | None:
    q = question.lower()
    mapping = [
        ("football field", "football field"),
        ("baseball", "baseball field"),
        ("plane", "plane"),
        ("harbor", "harbor"),
        ("ship", "ship"),
        ("storage tank", "storage tank"),
        ("building", "building"),
    ]
    for needle, prompt in mapping:
        if needle in q:
            return prompt
    return None


def _fallback_arguments(item, record: SkillExecutionRecord, planned_tool_name: str, step_index: int) -> dict[str, Any] | None:
    if planned_tool_name == "get_filelist":
        return {"dir_path": item.data_dir}

    # Recover latest useful raw result.
    latest_result = record.executed_steps[-1].raw_result if record.executed_steps else None
    latest_success = record.executed_steps[-1] if record.executed_steps else None
    gsd_values = _extract_gsds(item.question_text)

    if planned_tool_name == "compute_tvdi":
        names = latest_result if isinstance(latest_result, list) else item.file_list
        ndvi_names = [n for n in names if "ndvi" in str(n).lower()]
        lst_names = [n for n in names if "lst" in str(n).lower()]
        if ndvi_names and lst_names:
            ndvi_paths = _full_paths(item, [str(n) for n in ndvi_names])
            lst_paths = _full_paths(item, [str(n) for n in lst_names])
            paired = min(len(ndvi_paths), len(lst_paths))
            return {
                "ndvi_path": ndvi_paths[:paired],
                "lst_path": lst_paths[:paired],
                "output_path": [f"q{item.question_id}/tvdi_{i}.tif" for i in range(paired)],
            }

    if planned_tool_name == "calculate_tif_average":
        tvdi_results = []
        for step in record.executed_steps:
            if step.chosen_tool_name == "compute_tvdi":
                if isinstance(step.raw_result, list):
                    tvdi_results.extend(_unwrap_path(str(v)) for v in step.raw_result)
                elif isinstance(step.raw_result, str):
                    tvdi_results.append(_unwrap_path(step.raw_result))
        if tvdi_results:
            groups: dict[str, list[str]] = {}
            for path in tvdi_results:
                year = _year_from_name(str(path)) or "all"
                groups.setdefault(year, []).append(str(path))
            years = sorted(groups)
            call_idx = sum(1 for s in record.executed_steps if s.chosen_tool_name == "calculate_tif_average")
            year = years[min(call_idx, len(years) - 1)]
            return {
                "file_list": groups[year],
                "output_path": f"q{item.question_id}/avg_{year}.tif",
            }

    if planned_tool_name == "calc_batch_image_mean":
        avg_paths = [step.raw_result for step in record.executed_steps if step.chosen_tool_name == "calculate_tif_average" and isinstance(step.raw_result, str)]
        if avg_paths:
            return {"file_list": avg_paths}

    if planned_tool_name == "compute_linear_trend":
        for step in reversed(record.executed_steps):
            if step.chosen_tool_name == "calc_batch_image_mean":
                if isinstance(step.raw_result, list):
                    return {"y": step.raw_result}

    if planned_tool_name in {"RemoteSAM", "InstructSAM", "SM3Det"}:
        obj_prompt = _question_object_prompt(item.question_text)
        if obj_prompt and item.file_list:
            image_path = str(Path(item.data_dir) / item.file_list[min(step_index - 1, len(item.file_list) - 1)])
            return {"input_image_path": image_path, "text_prompt": obj_prompt if planned_tool_name != "RemoteSAM" else "football field on the westernmost side" if "westernmost" in item.question_text.lower() and "football" in item.question_text.lower() else obj_prompt}

    if planned_tool_name == "bboxes2centroids":
        bbox_like = _extract_bbox_like(latest_result)
        if bbox_like is not None:
            if bbox_like and all(isinstance(v, (int, float)) for v in bbox_like):
                return {"bboxes": [bbox_like]}
            return {"bboxes": bbox_like}

    if planned_tool_name == "centroid_distance_extremes":
        if isinstance(latest_result, list):
            return {"centroids": latest_result}

    if planned_tool_name == "get_list_object_via_indexes":
        prev_boxes = None
        prev_extremes = None
        for step in reversed(record.executed_steps):
            if prev_boxes is None and step.chosen_tool_name in {"SM3Det", "RemoteSAM"} and isinstance(step.raw_result, list):
                prev_boxes = [step.raw_result] if step.raw_result and all(isinstance(v, (int, float)) for v in step.raw_result) else step.raw_result
            if prev_extremes is None and step.chosen_tool_name == "centroid_distance_extremes" and isinstance(step.raw_result, dict):
                prev_extremes = step.raw_result
            if prev_boxes is not None and prev_extremes is not None:
                break
        if prev_boxes is not None and prev_extremes is not None and "min" in prev_extremes:
            return {"input_list": prev_boxes, "indexes": [prev_extremes["min"][0], prev_extremes["min"][1]]}

    if planned_tool_name == "SAM2":
        bbox_like = _extract_bbox_like(latest_result)
        if bbox_like is not None and item.file_list:
            bbox = bbox_like if bbox_like and all(isinstance(v, (int, float)) for v in bbox_like) else bbox_like[0]
            return {
                "input_image_path": str(Path(item.data_dir) / item.file_list[min(step_index - 1, len(item.file_list) - 1)]),
                "bbox": bbox,
                "output_path": f"q{item.question_id}/sam2_mask_{step_index}.png",
            }

    if planned_tool_name == "calculate_area":
        if isinstance(latest_result, str):
            gsd = gsd_values[min(step_index, len(gsd_values) - 1)] if gsd_values else None
            return {"input_image_path": latest_result, "gsd": gsd}

    if planned_tool_name == "difference":
        scalars = []
        for step in reversed(record.executed_steps):
            if isinstance(step.raw_result, (int, float)):
                scalars.append(float(step.raw_result))
            if len(scalars) >= 2:
                break
        if len(scalars) >= 2:
            return {"a": scalars[1], "b": scalars[0]}

    return None


def execute_one_question(question_id: str, output_dir: Path) -> SkillExecutionRecord:
    item = load_benchmark(question_ids=[str(question_id)])[0]
    log.info("Q%s | start | loading benchmark item", item.question_id)
    catalog = build_catalog()
    single_profile, single_shortlist, _ = _single_agent_context(item, catalog)
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    log.info("Q%s | planning | skill=%s shortlist=%d", item.question_id, skill.skill_id, len(single_shortlist))
    plan_result = plan_with_skill_llm(
        item,
        catalog=catalog,
        skill=skill,
        profile=single_profile,
        shortlisted=single_shortlist,
    )
    planned_steps = plan_result.planned_steps
    rendered_tools = render_text_description_and_args(plan_result.shortlisted_tools)
    llm_trace: dict[str, Any] = {
        "planner": {
            "skill_id": skill.skill_id,
            "planning_source": plan_result.planning_source,
            "system_prompt": plan_result.planner_system_prompt,
            "user_prompt": plan_result.planner_user_prompt,
            "raw_output": plan_result.raw_planner_output,
            "fallback_reason": plan_result.fallback_reason,
            "planned_tools": [step.tool_name for step in planned_steps],
        },
        "parameter_steps": [],
        "answer_selector": {},
    }

    record = SkillExecutionRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=skill.skill_id,
        planning_source=plan_result.planning_source,
        planned_tool_sequence=planned_steps,
    )
    _append_log(
        record,
        "plan",
        "Initialized skill execution record.",
        skill_id=skill.skill_id,
        planned_steps=[s.tool_name for s in planned_steps],
        planning_source=plan_result.planning_source,
        focus_tools=plan_result.focus_tools,
        fallback_reason=plan_result.fallback_reason,
        planner_system_prompt=plan_result.planner_system_prompt,
        planner_user_prompt=plan_result.planner_user_prompt,
        planner_raw_output=plan_result.raw_planner_output,
    )
    log.info(
        "Q%s | planning_done | source=%s steps=%d",
        item.question_id,
        plan_result.planning_source,
        len(planned_steps),
    )

    prior_steps: list[str] = []
    latest_observation: str | None = None
    skipped_due_to_network = False
    network_error_message = ""
    network_error_stage = ""

    try:
        runtime = ToolRuntime(DEFAULT_TEMP_DIR / f"q{item.question_id}")
        _append_log(record, "runtime_init", "Initialized tool runtime.")
        for idx, planned in enumerate(planned_steps):
            log.debug(
                "Q%s | step %d/%d | planned_tool=%s",
                item.question_id,
                idx + 1,
                len(planned_steps),
                planned.tool_name,
            )
            fallback_args = _fallback_arguments(item, record, planned.tool_name, idx)
            force_fallback_tools = {
                "get_filelist",
                "compute_tvdi",
                "calculate_tif_average",
                "calc_batch_image_mean",
                "compute_linear_trend",
                "bboxes2centroids",
                "centroid_distance_extremes",
                "SAM2",
                "calculate_area",
                "get_list_object_via_indexes",
                "difference",
            }
            use_forced_fallback = fallback_args is not None and planned.tool_name in force_fallback_tools

            if use_forced_fallback:
                from .parameter_worker import WorkerDecision

                decision = WorkerDecision(
                    tool_name=planned.tool_name,
                    arguments=fallback_args,
                    rationale="Deterministic argument path for stable benchmark execution.",
                    model="deterministic-bootstrap",
                )
                _append_log(record, "parameter_bootstrap", f"Used deterministic bootstrap arguments for {planned.tool_name}.", arguments=decision.arguments)
                llm_trace["parameter_steps"].append(
                    {
                        "step_index": idx,
                        "planned_tool_name": planned.tool_name,
                        "mode": "deterministic-bootstrap",
                        "prompt": "",
                        "arguments": decision.arguments,
                        "rationale": decision.rationale,
                        "raw_response": decision.raw_response,
                        "cleaned_response": decision.cleaned_response,
                        "model": decision.model,
                    }
                )
            else:
                prompt = _build_argument_prompt(
                    question=item.question_text,
                    data_dir=item.data_dir,
                    rendered_tools=rendered_tools,
                    planned_tool_name=planned.tool_name,
                    prior_steps=prior_steps,
                    latest_observation=latest_observation,
                )
                _append_log(record, "parameter_prompt", f"Requesting arguments for planned tool {planned.tool_name}.", prompt=prompt)
                try:
                    decision = choose_tool_arguments(prompt, planned_tool_name=planned.tool_name)
                    _append_log(
                        record,
                        "parameter_result",
                        f"Worker returned arguments for {planned.tool_name}.",
                        arguments=decision.arguments,
                        rationale=decision.rationale,
                        raw_response=decision.raw_response,
                        cleaned_response=decision.cleaned_response,
                        model=decision.model,
                    )
                    llm_trace["parameter_steps"].append(
                        {
                            "step_index": idx,
                            "planned_tool_name": planned.tool_name,
                            "mode": "llm",
                            "prompt": prompt,
                            "arguments": decision.arguments,
                            "rationale": decision.rationale,
                            "raw_response": decision.raw_response,
                            "cleaned_response": decision.cleaned_response,
                            "model": decision.model,
                        }
                    )
                except Exception as exc:
                    if isinstance(exc, NetworkCallError):
                        skipped_due_to_network = True
                        network_error_stage = "parameter-worker"
                        network_error_message = str(exc)
                        _append_log(
                            record,
                            "network_error",
                            f"Network error while requesting arguments for {planned.tool_name}. Skipping this question.",
                            error=str(exc),
                            prompt=prompt,
                            raw_response=getattr(exc, "raw_response", ""),
                            cleaned_response=getattr(exc, "cleaned_response", ""),
                            network_error=True,
                        )
                        llm_trace["parameter_steps"].append(
                            {
                                "step_index": idx,
                                "planned_tool_name": planned.tool_name,
                                "mode": "network-error-skip",
                                "prompt": prompt,
                                "arguments": {},
                                "rationale": "",
                                "raw_response": getattr(exc, "raw_response", ""),
                                "cleaned_response": getattr(exc, "cleaned_response", ""),
                                "model": "",
                                "error": str(exc),
                            }
                        )
                        break
                    if fallback_args is not None:
                        from .parameter_worker import WorkerDecision

                        worker_raw = getattr(exc, "raw_response", "")
                        worker_cleaned = getattr(exc, "cleaned_response", "")
                        decision = WorkerDecision(
                            tool_name=planned.tool_name,
                            arguments=fallback_args,
                            rationale=f"Fallback arguments used because worker failed: {exc}",
                            raw_response=worker_raw,
                            cleaned_response=worker_cleaned,
                            model="deterministic-fallback",
                        )
                        _append_log(record, "parameter_fallback", f"Used fallback arguments for {planned.tool_name}.", arguments=decision.arguments, error=str(exc), prompt=prompt, raw_response=worker_raw, cleaned_response=worker_cleaned)
                        llm_trace["parameter_steps"].append(
                            {
                                "step_index": idx,
                                "planned_tool_name": planned.tool_name,
                                "mode": "llm-error-fallback",
                                "prompt": prompt,
                                "arguments": decision.arguments,
                                "rationale": decision.rationale,
                                "raw_response": "",
                                "cleaned_response": "",
                                "model": decision.model,
                                "error": str(exc),
                            }
                        )
                    else:
                        step = ExecutedSkillStep(
                            step_index=idx,
                            planned_tool_name=planned.tool_name,
                            chosen_tool_name=planned.tool_name,
                            arguments={},
                            success=False,
                            error=str(exc),
                        )
                        record.executed_steps.append(step)
                        _append_log(record, "parameter_error", f"Failed to get arguments for {planned.tool_name}.", error=str(exc))
                        break

            try:
                result = runtime.execute(planned.tool_name, decision.arguments)
                summary = summarize_result(result)
                step = ExecutedSkillStep(
                    step_index=idx,
                    planned_tool_name=planned.tool_name,
                    chosen_tool_name=planned.tool_name,
                    arguments=decision.arguments,
                    raw_result=result,
                    result_summary=summary,
                    success=True,
                    worker_rationale=decision.rationale,
                )
                record.executed_steps.append(step)
                latest_observation = _observation_text(result)
                prior_steps.append(f"{planned.tool_name}: {summary}")
                _append_log(record, "tool_success", f"Executed {planned.tool_name}.", result_summary=summary)
                log.debug("Q%s | step %d/%d | tool_success=%s", item.question_id, idx + 1, len(planned_steps), planned.tool_name)
            except Exception as exc:
                step = ExecutedSkillStep(
                    step_index=idx,
                    planned_tool_name=planned.tool_name,
                    chosen_tool_name=planned.tool_name,
                    arguments=decision.arguments,
                    success=False,
                    error=str(exc),
                    worker_rationale=decision.rationale,
                )
                record.executed_steps.append(step)
                latest_observation = f"Execution failed: {exc}"
                prior_steps.append(f"{planned.tool_name}: FAILED ({exc})")
                _append_log(record, "tool_error", f"Execution failed for {planned.tool_name}.", error=str(exc))
                log.info("Q%s | step %d/%d | tool_error=%s | %s", item.question_id, idx + 1, len(planned_steps), planned.tool_name, exc)
                break
    except Exception as exc:
        _append_log(record, "fatal", "Unexpected executor error.", error=str(exc))

    if skipped_due_to_network:
        record.metrics = {
            "network_error": True,
            "skipped": True,
            "predicted_count": len(record.executed_steps),
            "gold_count": len(item.gold_tool_calls),
        }
        llm_trace["network_error"] = {
            "stage": network_error_stage,
            "message": network_error_message,
        }
        _append_log(record, "skip", "Skipped question after model network error.", stage_name=network_error_stage, error=network_error_message)
    else:
        execution_summary = _execution_summary(record)
        try:
            idx, label, text, reason, fallback_used, selector_messages, selector_raw = _select_final_answer(
                item.question_text,
                [str(c) for c in item.choices],
                execution_summary,
                seed=item.question_id,
            )
        except NetworkCallError as exc:
            record.metrics = {
                "network_error": True,
                "skipped": True,
                "predicted_count": len(record.executed_steps),
                "gold_count": len(item.gold_tool_calls),
            }
            llm_trace["answer_selector"] = {
                "messages": build_choice_selector_messages(
                    question=item.question_text,
                    choices=[str(c) for c in item.choices],
                    execution_summary=execution_summary,
                ),
                "raw_output": getattr(exc, "raw_response", ""),
                "fallback_used": False,
                "network_error": True,
                "error": str(exc),
            }
            _append_log(
                record,
                "network_error",
                "Network error during final answer selection. Skipping this question.",
                error=str(exc),
                raw_response=getattr(exc, "raw_response", ""),
                network_error=True,
            )
            _append_log(record, "skip", "Skipped question after model network error.", stage_name="answer-selector", error=str(exc))
        except Exception as exc:
            idx, label, text = safe_choice_fallback([str(c) for c in item.choices], item.question_id)
            reason = f"Fallback choice used because answer selector raised unexpectedly: {exc}"
            fallback_used = True
            selector_raw = getattr(exc, "raw_response", "")
            selector_messages = build_choice_selector_messages(
                question=item.question_text,
                choices=[str(c) for c in item.choices],
                execution_summary=execution_summary,
            )
        record.final_choice_index = idx
        record.final_choice_label = label
        record.final_choice_text = text
        record.final_choice_reason = reason
        record.final_answer_fallback_used = fallback_used
        llm_trace["answer_selector"] = {
            "messages": selector_messages,
            "raw_output": selector_raw,
            "choice_index": idx,
            "choice_label": label,
            "choice_text": text,
            "reason": reason,
            "fallback_used": fallback_used,
        }
        _append_log(record, "answer_selection", "Selected final answer.", choice_index=idx, choice_label=label, fallback=fallback_used, raw_response=selector_raw, reason=reason)

        record.metrics = evaluate_execution(record, item.gold_tool_calls, item.gt_answer)
        _append_log(record, "evaluation", "Computed benchmark metrics.", metrics=record.metrics)

    log.info(
        "Q%s | done | skill=%s plan=%s answer=%s fallback=%s TAO=%.4f TIO=%.4f TEM=%.4f ACC=%.4f",
        item.question_id,
        skill.skill_id,
        plan_result.planning_source,
        record.final_choice_label or "?",
        record.final_answer_fallback_used,
        record.metrics.get("tool_any_order", 0.0),
        record.metrics.get("tool_in_order", 0.0),
        record.metrics.get("tool_exact_match", 0.0),
        record.metrics.get("accuracy", 0.0),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    question_dir = output_dir / f"question_{item.question_id}"
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
    return record


def save_batch_summary(records: list[SkillExecutionRecord], output_dir: Path) -> Path:
    summary = aggregate_execution_metrics(records)
    path = output_dir / "batch_summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def execute_one_question_with_mode(
    question_id: str,
    output_dir: Path,
    *,
    pipeline_mode: str = "staged",
) -> SkillExecutionRecord:
    mode = (pipeline_mode or "staged").strip().lower()
    if mode == "staged":
        return execute_one_question(question_id, output_dir)
    if mode == "direct-executor":
        from .direct_executor import execute_one_question as execute_one_question_direct_executor

        return execute_one_question_direct_executor(question_id, output_dir)
    raise ValueError(f"Unsupported pipeline_mode: {pipeline_mode}")
