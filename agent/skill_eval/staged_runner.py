"""
Three-stage skill-eval pipeline helpers.

Stage 1: planning only
Stage 2: parameter generation + tool execution
Stage 3: final answer selection
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from .benchmark_loader import load_benchmark
from .config import DEFAULT_TEMP_DIR
from .evaluator import answer_accuracy, efficiency, parameter_accuracy, safe_choice_fallback, tool_any_order, tool_exact_match, tool_in_order
from .executor import (
    _append_log,
    _build_argument_prompt,
    _execution_summary,
    _fallback_arguments,
    _observation_text,
    _select_final_answer_async,
    _select_final_answer,
)
from .network_errors import NetworkCallError
from .parameter_worker import WorkerDecision, choose_tool_arguments
from .runtime import ToolRuntime, summarize_result
from .schemas import (
    BenchmarkItem,
    ExecutedSkillStep,
    PlannedSkillStep,
    SkillAnswerRecord,
    SkillParameterRecord,
    SkillPlanRecord,
)
from .skill_llm_planner import plan_with_skill_llm, plan_with_skill_llm_async
from .skill_router import route_skill
from .tool_catalog import ToolMeta, build_catalog
from .tool_router import infer_question_profile, shortlist_tools
from .tool_rendering import render_text_description_and_args

log = logging.getLogger(__name__)


def _load_item(question_id: str) -> BenchmarkItem:
    return load_benchmark(question_ids=[str(question_id)])[0]


def _select_shortlisted(catalog: list[ToolMeta], names: list[str]) -> list[ToolMeta]:
    allow = set(names)
    selected = [tool for tool in catalog if tool.canonical_name in allow]
    selected.sort(key=lambda t: names.index(t.canonical_name) if t.canonical_name in allow else 10**9)
    return selected


def _question_dir(output_dir: Path, qid: str) -> Path:
    path = output_dir / f"question_{qid}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def save_plan_question(record: SkillPlanRecord, llm_trace: dict[str, Any], output_dir: Path) -> None:
    qdir = _question_dir(output_dir, record.question_id)
    _save_json(qdir / "plan_record.json", asdict(record))
    _save_json(qdir / "plan_log.json", record.logs)
    _save_json(qdir / "planner_trace.json", llm_trace)


def save_parameter_question(record: SkillParameterRecord, llm_trace: dict[str, Any], output_dir: Path) -> None:
    qdir = _question_dir(output_dir, record.question_id)
    _save_json(qdir / "parameter_record.json", asdict(record))
    _save_json(qdir / "parameter_log.json", record.logs)
    _save_json(qdir / "parameter_trace.json", llm_trace)


def save_answer_question(record: SkillAnswerRecord, llm_trace: dict[str, Any], output_dir: Path) -> None:
    qdir = _question_dir(output_dir, record.question_id)
    _save_json(qdir / "answer_record.json", asdict(record))
    _save_json(qdir / "answer_log.json", record.logs)
    _save_json(qdir / "answer_trace.json", llm_trace)


def plan_one_question(question_id: str, *, catalog: list[ToolMeta] | None = None) -> tuple[SkillPlanRecord, dict[str, Any]]:
    item = _load_item(question_id)
    catalog = catalog or build_catalog()
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(catalog, item.question_text, item.file_list, question_id=item.question_id)
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    try:
        plan_result = plan_with_skill_llm(
            item,
            catalog=catalog,
            skill=skill,
            profile=profile,
            shortlisted=shortlisted,
        )
    except NetworkCallError as exc:
        record = SkillPlanRecord(
            question_id=item.question_id,
            question_text=item.question_text,
            data_dir=item.data_dir,
            skill_id=skill.skill_id,
            planning_source="network-error-skip",
            shortlisted_tool_names=[tool.canonical_name for tool in shortlisted],
            metrics={"network_error": True, "skipped": True},
        )
        _append_log(
            record,
            "network_error",
            "Network error during planning. Skipping this question.",
            error=str(exc),
            network_error=True,
        )
        llm_trace = {
            "planner": {
                "skill_id": skill.skill_id,
                "planning_source": "network-error-skip",
                "shortlisted_tool_names": record.shortlisted_tool_names,
                "focus_tools": [],
                "system_prompt": "",
                "user_prompt": "",
                "raw_output": getattr(exc, "raw_response", ""),
                "fallback_reason": str(exc),
                "planned_tools": [],
                "network_error": True,
            }
        }
        return record, llm_trace
    planned_tools = [step.tool_name for step in plan_result.planned_steps]
    gold_tools = item.gold_tool_names
    metrics = {
        "tool_any_order": round(tool_any_order(planned_tools, gold_tools), 4),
        "tool_in_order": round(tool_in_order(planned_tools, gold_tools), 4),
        "tool_exact_match": round(tool_exact_match(planned_tools, gold_tools), 4),
        "efficiency": round(efficiency(len(planned_tools), len(gold_tools)), 4),
        "predicted_count": len(planned_tools),
        "gold_count": len(gold_tools),
    }
    record = SkillPlanRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=skill.skill_id,
        planning_source=plan_result.planning_source,
        shortlisted_tool_names=[tool.canonical_name for tool in plan_result.shortlisted_tools],
        focus_tools=plan_result.focus_tools,
        planner_system_prompt=plan_result.planner_system_prompt,
        planner_user_prompt=plan_result.planner_user_prompt,
        planner_raw_output=plan_result.raw_planner_output,
        fallback_reason=plan_result.fallback_reason,
        planned_tool_sequence=plan_result.planned_steps,
        metrics=metrics,
    )
    _append_log(
        record,
        "plan",
        "Completed planning stage.",
        skill_id=skill.skill_id,
        planning_source=plan_result.planning_source,
        planned_steps=planned_tools,
        fallback_reason=plan_result.fallback_reason,
        shortlisted_tool_count=len(record.shortlisted_tool_names),
    )
    llm_trace = {
        "planner": {
            "skill_id": skill.skill_id,
            "planning_source": plan_result.planning_source,
            "shortlisted_tool_names": record.shortlisted_tool_names,
            "focus_tools": record.focus_tools,
            "system_prompt": plan_result.planner_system_prompt,
            "user_prompt": plan_result.planner_user_prompt,
            "raw_output": plan_result.raw_planner_output,
            "fallback_reason": plan_result.fallback_reason,
            "planned_tools": planned_tools,
        }
    }
    return record, llm_trace


async def plan_one_question_async(question_id: str, *, catalog: list[ToolMeta] | None = None) -> tuple[SkillPlanRecord, dict[str, Any]]:
    item = _load_item(question_id)
    catalog = catalog or build_catalog()
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(catalog, item.question_text, item.file_list, question_id=item.question_id)
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    try:
        plan_result = await plan_with_skill_llm_async(
            item,
            catalog=catalog,
            skill=skill,
            profile=profile,
            shortlisted=shortlisted,
        )
    except NetworkCallError as exc:
        record = SkillPlanRecord(
            question_id=item.question_id,
            question_text=item.question_text,
            data_dir=item.data_dir,
            skill_id=skill.skill_id,
            planning_source="network-error-skip",
            shortlisted_tool_names=[tool.canonical_name for tool in shortlisted],
            metrics={"network_error": True, "skipped": True},
        )
        _append_log(
            record,
            "network_error",
            "Network error during planning. Skipping this question.",
            error=str(exc),
            network_error=True,
        )
        llm_trace = {
            "planner": {
                "skill_id": skill.skill_id,
                "planning_source": "network-error-skip",
                "shortlisted_tool_names": record.shortlisted_tool_names,
                "focus_tools": [],
                "system_prompt": "",
                "user_prompt": "",
                "raw_output": getattr(exc, "raw_response", ""),
                "fallback_reason": str(exc),
                "planned_tools": [],
                "network_error": True,
            }
        }
        return record, llm_trace
    planned_tools = [step.tool_name for step in plan_result.planned_steps]
    gold_tools = item.gold_tool_names
    metrics = {
        "tool_any_order": round(tool_any_order(planned_tools, gold_tools), 4),
        "tool_in_order": round(tool_in_order(planned_tools, gold_tools), 4),
        "tool_exact_match": round(tool_exact_match(planned_tools, gold_tools), 4),
        "efficiency": round(efficiency(len(planned_tools), len(gold_tools)), 4),
        "predicted_count": len(planned_tools),
        "gold_count": len(gold_tools),
    }
    record = SkillPlanRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=skill.skill_id,
        planning_source=plan_result.planning_source,
        shortlisted_tool_names=[tool.canonical_name for tool in plan_result.shortlisted_tools],
        focus_tools=plan_result.focus_tools,
        planner_system_prompt=plan_result.planner_system_prompt,
        planner_user_prompt=plan_result.planner_user_prompt,
        planner_raw_output=plan_result.raw_planner_output,
        fallback_reason=plan_result.fallback_reason,
        planned_tool_sequence=plan_result.planned_steps,
        metrics=metrics,
    )
    _append_log(
        record,
        "plan",
        "Completed planning stage.",
        skill_id=skill.skill_id,
        planning_source=plan_result.planning_source,
        planned_steps=planned_tools,
        fallback_reason=plan_result.fallback_reason,
        shortlisted_tool_count=len(record.shortlisted_tool_names),
    )
    llm_trace = {
        "planner": {
            "skill_id": skill.skill_id,
            "planning_source": plan_result.planning_source,
            "shortlisted_tool_names": record.shortlisted_tool_names,
            "focus_tools": record.focus_tools,
            "system_prompt": plan_result.planner_system_prompt,
            "user_prompt": plan_result.planner_user_prompt,
            "raw_output": plan_result.raw_planner_output,
            "fallback_reason": plan_result.fallback_reason,
            "planned_tools": planned_tools,
        }
    }
    return record, llm_trace


def load_plan_record(input_dir: Path, question_id: str) -> SkillPlanRecord:
    path = input_dir / f"question_{question_id}" / "plan_record.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["planned_tool_sequence"] = [PlannedSkillStep(**step) for step in data.get("planned_tool_sequence", [])]
    return SkillPlanRecord(**data)


def parameterize_one_question(plan_record: SkillPlanRecord, *, catalog: list[ToolMeta] | None = None) -> tuple[SkillParameterRecord, dict[str, Any]]:
    item = _load_item(plan_record.question_id)
    catalog = catalog or build_catalog()
    shortlisted = _select_shortlisted(catalog, plan_record.shortlisted_tool_names)
    rendered_tools = render_text_description_and_args(shortlisted)
    record = SkillParameterRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=plan_record.skill_id,
        planning_source=plan_record.planning_source,
        shortlisted_tool_names=plan_record.shortlisted_tool_names,
        planned_tool_sequence=plan_record.planned_tool_sequence,
    )
    parameter_trace: dict[str, Any] = {
        "parameter_steps": [],
        "runtime": {},
    }
    _append_log(
        record,
        "parameter_stage",
        "Initialized parameter stage.",
        planned_steps=[step.tool_name for step in plan_record.planned_tool_sequence],
    )
    prior_steps: list[str] = []
    latest_observation: str | None = None
    skipped_due_to_network = False
    network_error_message = ""

    try:
        runtime = ToolRuntime(DEFAULT_TEMP_DIR / f"q{item.question_id}")
        parameter_trace["runtime"] = {"initialized": True, "temp_dir": str(DEFAULT_TEMP_DIR / f'q{item.question_id}')}
        _append_log(record, "runtime_init", "Initialized tool runtime.")
        for idx, planned in enumerate(plan_record.planned_tool_sequence):
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
                decision = WorkerDecision(
                    tool_name=planned.tool_name,
                    arguments=fallback_args,
                    rationale="Deterministic argument path for stable benchmark execution.",
                    model="deterministic-bootstrap",
                )
                parameter_trace["parameter_steps"].append(
                    {
                        "step_index": idx,
                        "planned_tool_name": planned.tool_name,
                        "mode": "deterministic-bootstrap",
                        "prompt": "",
                        "arguments": decision.arguments,
                        "rationale": decision.rationale,
                        "raw_response": "",
                        "cleaned_response": "",
                        "model": decision.model,
                    }
                )
                _append_log(record, "parameter_bootstrap", f"Used deterministic bootstrap arguments for {planned.tool_name}.", arguments=decision.arguments)
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
                    parameter_trace["parameter_steps"].append(
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
                    _append_log(record, "parameter_result", f"Worker returned arguments for {planned.tool_name}.", arguments=decision.arguments, rationale=decision.rationale, raw_response=decision.raw_response, cleaned_response=decision.cleaned_response, model=decision.model)
                except Exception as exc:
                    if isinstance(exc, NetworkCallError):
                        skipped_due_to_network = True
                        network_error_message = str(exc)
                        parameter_trace["parameter_steps"].append(
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
                        _append_log(record, "network_error", f"Network error while requesting arguments for {planned.tool_name}. Skipping this question.", error=str(exc), prompt=prompt, raw_response=getattr(exc, "raw_response", ""), cleaned_response=getattr(exc, "cleaned_response", ""), network_error=True)
                        break
                    if fallback_args is not None:
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
                        parameter_trace["parameter_steps"].append(
                            {
                                "step_index": idx,
                                "planned_tool_name": planned.tool_name,
                                "mode": "llm-error-fallback",
                                "prompt": prompt,
                                "arguments": decision.arguments,
                                "rationale": decision.rationale,
                                "raw_response": worker_raw,
                                "cleaned_response": worker_cleaned,
                                "model": decision.model,
                                "error": str(exc),
                            }
                        )
                        _append_log(record, "parameter_fallback", f"Used fallback arguments for {planned.tool_name}.", arguments=decision.arguments, error=str(exc), prompt=prompt, raw_response=worker_raw, cleaned_response=worker_cleaned)
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

            if skipped_due_to_network:
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
                break
    except Exception as exc:
        parameter_trace["runtime"] = {
            "initialized": False,
            "error": str(exc),
        }
        _append_log(record, "fatal", "Unexpected parameter-stage error.", error=str(exc))

    predicted_calls = [
        {"name": step.chosen_tool_name, "arguments": step.arguments}
        for step in record.executed_steps
    ]
    record.execution_summary = _execution_summary(record)
    if skipped_due_to_network:
        record.metrics = {
            "network_error": True,
            "skipped": True,
            "executed_count": len(record.executed_steps),
            "gold_count": len(item.gold_tool_calls),
        }
        parameter_trace["network_error"] = {"message": network_error_message}
        _append_log(record, "skip", "Skipped question after model network error.", error=network_error_message)
    else:
        record.metrics = {
            "parameter_accuracy": round(parameter_accuracy(predicted_calls, item.gold_tool_calls), 4),
            "executed_count": len(record.executed_steps),
            "gold_count": len(item.gold_tool_calls),
        }
        _append_log(record, "parameter_evaluation", "Computed parameter-stage metrics.", metrics=record.metrics)
    return record, parameter_trace


def load_parameter_record(input_dir: Path, question_id: str) -> SkillParameterRecord:
    path = input_dir / f"question_{question_id}" / "parameter_record.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["planned_tool_sequence"] = [PlannedSkillStep(**step) for step in data.get("planned_tool_sequence", [])]
    data["executed_steps"] = [ExecutedSkillStep(**step) for step in data.get("executed_steps", [])]
    return SkillParameterRecord(**data)


def answer_one_question(parameter_record: SkillParameterRecord) -> tuple[SkillAnswerRecord, dict[str, Any]]:
    item = _load_item(parameter_record.question_id)
    record = SkillAnswerRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=parameter_record.skill_id,
        planning_source=parameter_record.planning_source,
        execution_summary=parameter_record.execution_summary,
    )
    _append_log(record, "answer_stage", "Initialized answer stage.")
    try:
        idx, label, text, reason, fallback_used, selector_messages, selector_raw = _select_final_answer(
            item.question_text,
            [str(c) for c in item.choices],
            parameter_record.execution_summary,
            seed=item.question_id,
        )
    except NetworkCallError as exc:
        record.metrics = {"network_error": True, "skipped": True}
        _append_log(record, "network_error", "Network error during final answer selection. Skipping this question.", error=str(exc), raw_response=getattr(exc, "raw_response", ""), network_error=True)
        llm_trace = {
            "answer_selector": {
                "messages": [],
                "raw_output": getattr(exc, "raw_response", ""),
                "fallback_used": False,
                "network_error": True,
                "error": str(exc),
            }
        }
        return record, llm_trace
    except Exception as exc:
        idx, label, text = safe_choice_fallback([str(c) for c in item.choices], item.question_id)
        reason = f"Fallback choice used because answer stage raised unexpectedly: {exc}"
        fallback_used = True
        selector_messages = []
        selector_raw = getattr(exc, "raw_response", "")
    record.final_choice_index = idx
    record.final_choice_label = label
    record.final_choice_text = text
    record.final_choice_reason = reason
    record.final_answer_fallback_used = fallback_used
    record.metrics = {
        "accuracy": round(answer_accuracy(label, item.gt_answer), 4),
    }
    _append_log(record, "answer_selection", "Selected final answer.", choice_index=idx, choice_label=label, fallback=fallback_used, reason=reason, raw_response=selector_raw)
    _append_log(record, "answer_evaluation", "Computed answer-stage accuracy.", metrics=record.metrics)
    llm_trace = {
        "answer_selector": {
            "messages": selector_messages,
            "raw_output": selector_raw,
            "choice_index": idx,
            "choice_label": label,
            "choice_text": text,
            "reason": reason,
            "fallback_used": fallback_used,
        }
    }
    return record, llm_trace


async def answer_one_question_async(parameter_record: SkillParameterRecord) -> tuple[SkillAnswerRecord, dict[str, Any]]:
    item = _load_item(parameter_record.question_id)
    record = SkillAnswerRecord(
        question_id=item.question_id,
        question_text=item.question_text,
        data_dir=item.data_dir,
        skill_id=parameter_record.skill_id,
        planning_source=parameter_record.planning_source,
        execution_summary=parameter_record.execution_summary,
    )
    _append_log(record, "answer_stage", "Initialized answer stage.")
    try:
        idx, label, text, reason, fallback_used, selector_messages, selector_raw = await _select_final_answer_async(
            item.question_text,
            [str(c) for c in item.choices],
            parameter_record.execution_summary,
            seed=item.question_id,
        )
    except NetworkCallError as exc:
        record.metrics = {"network_error": True, "skipped": True}
        _append_log(record, "network_error", "Network error during final answer selection. Skipping this question.", error=str(exc), raw_response=getattr(exc, "raw_response", ""), network_error=True)
        llm_trace = {
            "answer_selector": {
                "messages": [],
                "raw_output": getattr(exc, "raw_response", ""),
                "fallback_used": False,
                "network_error": True,
                "error": str(exc),
            }
        }
        return record, llm_trace
    except Exception as exc:
        idx, label, text = safe_choice_fallback([str(c) for c in item.choices], item.question_id)
        reason = f"Fallback choice used because answer stage raised unexpectedly: {exc}"
        fallback_used = True
        selector_messages = []
        selector_raw = getattr(exc, "raw_response", "")
    record.final_choice_index = idx
    record.final_choice_label = label
    record.final_choice_text = text
    record.final_choice_reason = reason
    record.final_answer_fallback_used = fallback_used
    record.metrics = {
        "accuracy": round(answer_accuracy(label, item.gt_answer), 4),
    }
    _append_log(record, "answer_selection", "Selected final answer.", choice_index=idx, choice_label=label, fallback=fallback_used, reason=reason, raw_response=selector_raw)
    _append_log(record, "answer_evaluation", "Computed answer-stage accuracy.", metrics=record.metrics)
    llm_trace = {
        "answer_selector": {
            "messages": selector_messages,
            "raw_output": selector_raw,
            "choice_index": idx,
            "choice_label": label,
            "choice_text": text,
            "reason": reason,
            "fallback_used": fallback_used,
        }
    }
    return record, llm_trace
