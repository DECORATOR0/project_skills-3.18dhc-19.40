#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import logging
import math
import numbers
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tqdm import tqdm

from nlrl_skills.config import load_system_config
from nlrl_skills.data import ensure_required_gold_overrides_loaded, load_converted_dataset
from nlrl_skills.evaluation import evaluate_execution
from nlrl_skills.schemas import EnvRunResult, EnvState, ToolCallRecord, to_dict
from nlrl_skills.tools import ToolContext, Toolbox
from nlrl_skills.utils import ensure_dir, prepare_dated_run_dir, write_json

log = logging.getLogger(__name__)

DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "system-train-xsy-gpt54-jh-v6-promptsnapshot-goldfix248-dataalignv1.json"
EVALUATION_MODE = "strict-gold-replay"


def setup_logging(output_dir: Path, verbose: bool = False) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    handlers = [logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    return log_path


def _select_tasks(tasks: list[Any], args: argparse.Namespace) -> list[Any]:
    if args.question:
        qids = {str(args.question)}
    elif args.question_file:
        qids = {
            line.strip()
            for line in Path(args.question_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    elif args.start is not None:
        end = args.end or args.start
        qids = {str(i) for i in range(args.start, end + 1)}
    else:
        return tasks

    return [
        t for t in tasks
        if str(t.metadata.get("original_question_id", "")) in qids or t.task_id in qids
    ]


def _metric_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    metric_keys = [
        "accuracy",
        "efficiency",
        "tool_any_order",
        "tool_in_order",
        "tool_exact_match",
        "parameter_accuracy",
    ]
    aliases = {
        "TAO": "tool_any_order",
        "TIO": "tool_in_order",
        "TEM": "tool_exact_match",
        "Efficiency": "efficiency",
        "Parameters": "parameter_accuracy",
        "Accuracy": "accuracy",
    }
    summary: dict[str, Any] = {
        "count": len(results),
        "tool_replay_success_count": sum(1 for item in results if item.get("tool_replay_success")),
        "answer_match_count": sum(1 for item in results if item["metrics"].get("task_success")),
        "stopped_early_count": sum(1 for item in results if item.get("stopped_early")),
        "evaluation_mode": EVALUATION_MODE,
    }
    for key in metric_keys:
        values = [float(item["metrics"].get(key, 0.0)) for item in results]
        summary[f"avg_{key}"] = round(sum(values) / len(values), 4) if values else 0.0
    summary["named_avg_metrics"] = {
        name: summary[f"avg_{metric}"] for name, metric in aliases.items()
    }
    return summary


def _parse_arguments(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {"raw": payload}
    return {}


def _extract_expected_tool_content(turn: dict[str, Any]) -> Any:
    content = turn.get("content")
    if isinstance(content, dict) and "content" in content:
        return content.get("content")
    return content


def _normalize_tool_name(name: str) -> str:
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _artifact_suffix(text: str) -> str:
    normalized = str(text).strip().replace("\\", "/")
    match = re.search(r"(question\d+/.+)$", normalized)
    if match:
        return match.group(1)
    match = re.search(r"(benchmark/(?:out|data)/.+)$", normalized)
    if match:
        return match.group(1)
    return Path(normalized).name or normalized


def _canonicalize_result(value: Any) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {"type", "content"}:
        return _canonicalize_result(value["content"])
    if isinstance(value, dict):
        if set(value.keys()) <= {"__saved_artifact__", "__path__"}:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if isinstance(item, str):
                    normalized[key] = _artifact_suffix(item)
                else:
                    normalized[key] = item
            return normalized
        return {str(k): _canonicalize_result(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_result(v) for v in value]
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("\\", "/")
        if text.startswith("Result save at "):
            return {"__saved_artifact__": _artifact_suffix(text[len("Result save at "):])}
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
            return float(text)
        if "/" in text and re.search(r"\.(?:tif|png|jpg|jpeg|json|txt)$", text, flags=re.IGNORECASE):
            return {"__path__": _artifact_suffix(text)}
        return text
    return value


def _compare_canonical(left: Any, right: Any, *, tol: float = 1e-4) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return math.isclose(left, right, rel_tol=0.0, abs_tol=tol)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_compare_canonical(l, r, tol=tol) for l, r in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left.keys()) != set(right.keys()):
            return False
        return all(_compare_canonical(left[key], right[key], tol=tol) for key in left)
    return left == right


def _values_match(actual: Any, expected: Any, *, tol: float = 1e-4) -> bool:
    left = _canonicalize_result(actual)
    right = _canonicalize_result(expected)
    return _compare_canonical(left, right, tol=tol)


def _json_preview(value: Any, *, limit: int = 800) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _sync_benchmark_out_compat(runtime_root: Path, compat_root: Path, question_dir: str) -> int:
    synced = 0
    if not runtime_root.exists():
        return synced

    for module_dir in sorted(path for path in runtime_root.iterdir() if path.is_dir()):
        for src in module_dir.rglob("*"):
            if not (src.is_file() or src.is_symlink()):
                continue
            rel = src.relative_to(module_dir)
            rel_text = str(rel).replace("\\", "/")
            if f"{question_dir}/" not in rel_text:
                continue
            if rel_text.startswith("benchmark/out/"):
                dst = compat_root.parents[1] / rel
            else:
                dst = compat_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink() or dst.exists():
                try:
                    if dst.resolve() == src.resolve():
                        continue
                except FileNotFoundError:
                    pass
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            dst.symlink_to(src)
            synced += 1
    return synced


def _cleanup_compat_outputs(compat_root: Path, question_ids: list[str]) -> None:
    for qid in question_ids:
        for target in (
            compat_root / f"question{qid}",
            compat_root / "benchmark" / "out" / f"question{qid}",
        ):
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)


def _strict_replay_task(
    task: Any,
    toolbox: Toolbox,
    run_dir: Path,
    *,
    compat_root: Path,
    runtime_root: Path,
) -> tuple[EnvState, dict[str, Any]]:
    ensure_dir(run_dir)
    toolbox.set_active_skill_dir(None)
    toolbox.set_active_task_data_dir(task.data_dir)
    qid = str(task.metadata.get("original_question_id", task.task_id))
    question_dir = f"question{qid}"

    step_index = 0
    raw_lines: list[str] = []
    tool_records: list[ToolCallRecord] = []
    final_answer = ""
    failure: dict[str, Any] | None = None

    try:
        turns = task.gold_trajectory
        turn_index = 0
        while turn_index < len(turns):
            turn = turns[turn_index]
            role = turn.get("role")

            if role == "assistant":
                thought = str(turn.get("thought") or "")
                tool_calls = turn.get("tool_calls") or []
                content = turn.get("content")

                if tool_calls:
                    expected_tool_turns: list[dict[str, Any]] = []
                    lookahead = turn_index + 1
                    while lookahead < len(turns) and turns[lookahead].get("role") == "tool" and len(expected_tool_turns) < len(tool_calls):
                        expected_tool_turns.append(turns[lookahead])
                        lookahead += 1
                    if len(expected_tool_turns) < len(tool_calls):
                        failure = {
                            "failure_mode": "gold_trajectory_malformed",
                            "turn_index": turn_index,
                            "error": f"Assistant turn at index {turn_index} has {len(tool_calls)} tool calls but only {len(expected_tool_turns)} following tool turns.",
                        }
                        raw_lines.append(f"[FAIL] malformed gold trajectory at turn {turn_index}")
                        break

                    for call_offset, tool_call in enumerate(tool_calls):
                        step_index += 1
                        function = tool_call.get("function", {})
                        tool_name = str(function.get("name", "")).strip()
                        arguments = _parse_arguments(function.get("arguments", {}))
                        expected_tool_turn = expected_tool_turns[call_offset]
                        expected_tool_name = str(expected_tool_turn.get("name", "")).strip()
                        expected_content = _extract_expected_tool_content(expected_tool_turn)

                        if expected_tool_name and _normalize_tool_name(expected_tool_name) != _normalize_tool_name(tool_name):
                            failure = {
                                "failure_mode": "gold_trajectory_malformed",
                                "step_index": step_index,
                                "tool_name": tool_name,
                                "expected_tool_name": expected_tool_name,
                                "error": "Tool turn name does not match assistant tool call name.",
                            }
                            raw_lines.append(
                                f"[FAIL] step {step_index} tool name mismatch: call={tool_name} expected_tool_turn={expected_tool_name}"
                            )
                            break

                        raw_lines.append(
                            f"[CALL] step {step_index} {tool_name} args={_json_preview(arguments, limit=500)}"
                        )
                        try:
                            result = toolbox.execute(tool_name, arguments)
                            synced = _sync_benchmark_out_compat(runtime_root, compat_root, question_dir)
                            observation = _json_preview(result)
                            tool_records.append(
                                ToolCallRecord(
                                    step_index=step_index,
                                    thought=thought,
                                    tool_name=tool_name,
                                    arguments=arguments,
                                    observation=observation,
                                    success=True,
                                    raw_result=result,
                                    error="",
                                )
                            )
                            raw_lines.append(
                                f"[OK] step {step_index} {tool_name} -> {_json_preview(result, limit=600)}"
                            )
                            if synced:
                                raw_lines.append(
                                    f"[SYNC] step {step_index} compat benchmark/out refreshed files={synced}"
                                )
                        except Exception as exc:
                            tool_records.append(
                                ToolCallRecord(
                                    step_index=step_index,
                                    thought=thought,
                                    tool_name=tool_name,
                                    arguments=arguments,
                                    observation=_json_preview({"error": str(exc)}),
                                    success=False,
                                    raw_result=None,
                                    error=str(exc),
                                )
                            )
                            failure = {
                                "failure_mode": "tool_error",
                                "step_index": step_index,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "error": str(exc),
                            }
                            raw_lines.append(f"[FAIL] step {step_index} {tool_name} error={exc}")
                            break

                        if not _values_match(result, expected_content):
                            failure = {
                                "failure_mode": "tool_output_mismatch",
                                "step_index": step_index,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "expected_observation": expected_content,
                                "actual_observation": result,
                                "error": "Observed tool output does not match gold tool output.",
                            }
                            raw_lines.append(
                                "[FAIL] step "
                                f"{step_index} {tool_name} output mismatch expected={_json_preview(expected_content, limit=500)} "
                                f"actual={_json_preview(result, limit=500)}"
                            )
                            break

                    if failure is not None:
                        break
                    turn_index = lookahead
                    continue

                if content is not None:
                    final_answer = str(content).strip()
                    raw_lines.append(f"[ANSWER] {final_answer}")

            turn_index += 1

        choice_label = final_answer if len(final_answer) == 1 else ""
        evaluation = evaluate_execution(
            final_choice_label=choice_label,
            final_answer=final_answer,
            executed_steps=tool_records,
            gold_tool_names=task.gold_tool_names,
            gold_trajectory=task.gold_trajectory,
            gold_answer=task.gold_answer,
        )
        if failure is None and final_answer and final_answer.strip().upper() != task.gold_answer.strip().upper():
            failure = {
                "failure_mode": "final_answer_mismatch",
                "error": "Gold trajectory final answer does not match task.gold_answer.",
                "expected_answer": task.gold_answer,
                "actual_answer": final_answer,
            }
            raw_lines.append(
                f"[FAIL] final answer mismatch expected={task.gold_answer} actual={final_answer}"
            )

        replay_summary = (
            "Strict gold replay completed without mismatch."
            if failure is None
            else f"Strict gold replay stopped: {failure.get('failure_mode')} | {failure.get('error', '')}"
        )
        env_result = EnvRunResult(
            final_answer=final_answer,
            final_choice_label=choice_label,
            tool_trajectory=tool_records,
            phase_transitions=[],
            executor_summary=replay_summary,
            raw_executor_output="\n".join(raw_lines),
            evaluation=evaluation,
        )
        state = EnvState(
            task_id=task.task_id,
            task_prompt=task.prompt,
            env_result=env_result,
            gold_trajectory=task.gold_trajectory,
            gold_tool_names=task.gold_tool_names,
            active_skill_name="strict-gold-replay",
            task_context={
                "data_dir": task.data_dir,
                "file_list": task.file_list,
                "choices": task.choices,
                "gold_answer": task.gold_answer,
                "evaluation_mode": EVALUATION_MODE,
            },
        )
        write_json(run_dir / "state.json", to_dict(state))
        diagnostics = {
            "evaluation_mode": EVALUATION_MODE,
            "tool_replay_success": failure is None,
            "stopped_early": failure is not None,
            "failure": failure or {},
            "executed_tool_count": len(tool_records),
            "gold_tool_count": len(task.gold_tool_names),
            "final_answer": final_answer,
            "gold_answer": task.gold_answer,
        }
        write_json(run_dir / "replay_diagnostics.json", diagnostics)
        return state, diagnostics
    finally:
        toolbox.set_active_task_data_dir(None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Strictly replay gold trajectories and stop each task on the first mismatch or tool error.")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG), help="Path to local system config.")
    parser.add_argument("--run-name", type=str, default=None, help="Optional dated run name.")
    parser.add_argument("--question-file", type=str, help="File with one question ID per line.")
    parser.add_argument("--question", type=str, help="Single question ID.")
    parser.add_argument("--start", type=int, help="Start question ID.")
    parser.add_argument("--end", type=int, help="End question ID.")
    parser.add_argument("--all", action="store_true", help="Replay all questions.")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent task workers.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not any([args.question_file, args.question, args.start is not None, args.all]):
        parser.error("one of --question-file / --question / --start / --all is required")

    config = load_system_config(args.config)
    run_name = args.run_name or f"strict_gold_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = prepare_dated_run_dir(config.run_root, run_name=run_name)
    log_path = setup_logging(run_dir, args.verbose)
    compat_root = config.workspace_root / "benchmark" / "out"
    runtime_root = config.run_root / "temp" / "eo_runtime"

    tasks = load_converted_dataset(config.converted_dataset_path, config.gold_overrides_path)
    tasks = _select_tasks(tasks, args)
    if not tasks:
        raise SystemExit("No tasks matched the selection criteria.")

    ensure_required_gold_overrides_loaded(
        tasks,
        workspace_root=config.workspace_root,
        active_override_path=config.gold_overrides_path,
    )

    question_ids = [str(task.metadata.get("original_question_id", task.task_id)) for task in tasks]
    _cleanup_compat_outputs(compat_root, question_ids)
    write_json(run_dir / "run_metadata.json", {"evaluation_mode": EVALUATION_MODE, "run_dir": str(run_dir)})
    write_json(
        run_dir / "pipeline_context.json",
        {
            "config_path": str(Path(args.config).resolve()),
            "question_file": str(Path(args.question_file).resolve()) if args.question_file else "",
            "question_ids": question_ids,
            "task_count": len(tasks),
            "evaluation_mode": EVALUATION_MODE,
            "executor_model_requested": "gpt-5.2",
            "executor_model_used": "",
            "notes": "Strict gold trajectory replay. The LLM is bypassed; tool calls are executed exactly as recorded in gold_trajectory and compared against gold tool outputs. Runtime outputs are also synced into benchmark/out as compatibility symlinks during replay.",
            "eval_concurrency": args.concurrency,
        },
    )
    write_json(run_dir / "config_snapshot.json", {"config": to_dict(config)})

    log.info("Run dir: %s", run_dir)
    log.info("Evaluation mode: %s", EVALUATION_MODE)
    log.info("Strict replay bypasses the LLM and executes gold tool calls directly.")
    log.info("Compat benchmark/out root: %s", compat_root)
    log.info("Runtime artifact root: %s", runtime_root)
    log.info("Selected %d tasks: %s", len(tasks), ", ".join(question_ids))

    primary_toolbox = Toolbox(
        ToolContext(
            workspace_root=config.workspace_root,
            skill_library_root=config.skill_library_root,
            temp_root=config.run_root / "temp",
            python_executable=config.runtime.python_executable,
            shell_program=config.runtime.shell_program,
        )
    )
    shared_eo_runtime = primary_toolbox.context.eo_runtime

    total = len(tasks)
    results: list[dict[str, Any] | None] = [None] * total
    concurrency = max(1, min(args.concurrency, total))

    def _run_one(idx: int, task: Any) -> dict[str, Any]:
        qid = str(task.metadata.get("original_question_id", task.task_id))
        task_dir = ensure_dir(run_dir / f"task_{idx:02d}_{qid}")
        write_json(task_dir / "task.json", to_dict(task))
        toolbox = Toolbox(
            ToolContext(
                workspace_root=config.workspace_root,
                skill_library_root=config.skill_library_root,
                temp_root=config.run_root / "temp",
                python_executable=config.runtime.python_executable,
                shell_program=config.runtime.shell_program,
                eo_runtime=shared_eo_runtime,
            )
        )
        log.info("[%d/%d] Strict replay Q%s", idx, total, qid)
        try:
            state, diagnostics = _strict_replay_task(
                task,
                toolbox,
                task_dir / "replay",
                compat_root=compat_root,
                runtime_root=runtime_root,
            )
            evaluation = state.env_result.evaluation
            result_entry = {
                "task_id": task.task_id,
                "original_question_id": qid,
                "evaluation_mode": EVALUATION_MODE,
                "tool_replay_success": diagnostics["tool_replay_success"],
                "stopped_early": diagnostics["stopped_early"],
                "failure": diagnostics["failure"],
                "metrics": {
                    "accuracy": evaluation.accuracy,
                    "efficiency": evaluation.efficiency,
                    "tool_any_order": evaluation.tool_any_order,
                    "tool_in_order": evaluation.tool_in_order,
                    "tool_exact_match": evaluation.tool_exact_match,
                    "parameter_accuracy": evaluation.parameter_accuracy,
                    "task_success": evaluation.task_success,
                    "notes": evaluation.notes,
                },
                "final_answer": state.env_result.final_answer,
                "final_choice_label": state.env_result.final_choice_label,
                "replay_summary": state.env_result.executor_summary,
                "executed_tool_count": diagnostics["executed_tool_count"],
                "gold_tool_count": diagnostics["gold_tool_count"],
            }
            write_json(task_dir / "evaluation_summary.json", result_entry)
            log.info(
                "[%d/%d] Q%s | tool_replay_success=%s | answer_match=%s | failure_mode=%s",
                idx,
                total,
                qid,
                diagnostics["tool_replay_success"],
                evaluation.task_success,
                diagnostics["failure"].get("failure_mode", ""),
            )
            return result_entry
        except Exception as exc:
            failure = {
                "task_id": task.task_id,
                "original_question_id": qid,
                "evaluation_mode": EVALUATION_MODE,
                "tool_replay_success": False,
                "stopped_early": True,
                "failure": {
                    "failure_mode": "runner_error",
                    "error": str(exc),
                },
                "metrics": {
                    "accuracy": 0.0,
                    "efficiency": 0.0,
                    "tool_any_order": 0.0,
                    "tool_in_order": 0.0,
                    "tool_exact_match": 0.0,
                    "parameter_accuracy": 0.0,
                    "task_success": False,
                    "notes": "Strict replay runner failure.",
                    "error": str(exc),
                },
                "final_answer": "",
                "final_choice_label": "",
                "replay_summary": f"Runner failure: {exc}",
            }
            write_json(task_dir / "evaluation_failure.json", failure)
            write_json(task_dir / "evaluation_summary.json", failure)
            log.error("[%d/%d] Q%s strict replay failed: %s", idx, total, qid, exc, exc_info=True)
            return failure

    log.info("Launching %d concurrent strict replay workers", concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx = {
            pool.submit(_run_one, idx, task): idx - 1
            for idx, task in enumerate(tasks, 1)
        }
        with tqdm(total=total, desc="StrictReplay", unit="task") as pbar:
            for future in as_completed(future_to_idx):
                slot = future_to_idx[future]
                results[slot] = future.result()
                pbar.update(1)

    results = [item for item in results if item is not None]
    summary = _metric_summary(results)
    summary.update(
        {
            "run_dir": str(run_dir),
            "question_ids": question_ids,
            "executor_model_requested": "gpt-5.2",
            "executor_model_used": "",
        }
    )
    write_json(run_dir / "records.json", results)
    write_json(run_dir / "evaluation_summary.json", summary)
    log.info("Batch summary: %s", json.dumps(summary, ensure_ascii=False))
    print(f"Logs written to {log_path}")
    print(f"Run dir: {run_dir}")


if __name__ == "__main__":
    main()
