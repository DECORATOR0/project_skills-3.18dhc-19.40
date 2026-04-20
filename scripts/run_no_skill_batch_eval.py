#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tqdm import tqdm

from nlrl_skills.config import load_system_config
from nlrl_skills.data import ensure_required_gold_overrides_loaded, load_converted_dataset
from nlrl_skills.environment import SkillEnvironment
from nlrl_skills.schemas import DatasetTask, SkillDetail, SkillHeader, SkillPhase, to_dict
from nlrl_skills.utils import ensure_dir, prepare_dated_run_dir, write_json

log = logging.getLogger(__name__)

DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "system-train-xsy-gpt54-jh-v6-promptsnapshot-goldfix248-dataalignv1.json"
EVALUATION_MODE = "no-skill-executor"


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
    for noisy_name in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy_name).setLevel(logging.WARNING)
    return log_path


def _select_tasks(tasks: list[DatasetTask], args: argparse.Namespace) -> list[DatasetTask]:
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


def _build_no_skill_detail(run_dir: Path, allowed_tools: list[str]) -> SkillDetail:
    skill_dir = ensure_dir(run_dir / "_no_skill_virtual_skill")
    phases = {
        "INIT": SkillPhase(
            name="INIT",
            order=0,
            content=(
                "Inspect the task prompt, choices, and data directory.\n"
                "Start by calling `get_filelist` on the task data directory so you can see the exact filenames.\n"
                "Then move to IDENTIFY.\n\n"
                "Available actions:\n"
                "- <CALL>get_filelist</CALL><ARGS>{\"dir_path\": \"DATA_DIR\"}</ARGS>\n"
                "- <NEXT>IDENTIFY</NEXT>"
            ),
        ),
        "IDENTIFY": SkillPhase(
            name="IDENTIFY",
            order=1,
            content=(
                "Choose the processing phase that best matches the task.\n"
                "Use PROCESS_SPECTRUM for scientific raster / thermal / index-computation tasks.\n"
                "Use PROCESS_PRODUCTS for derived products, time series, arithmetic, and hotspot/change-map tasks.\n"
                "Use PROCESS_RGB for scene classification, object counting, grounding, geometry, and pre/post image change tasks.\n\n"
                "Available actions:\n"
                "- <NEXT>PROCESS_SPECTRUM</NEXT>\n"
                "- <NEXT>PROCESS_PRODUCTS</NEXT>\n"
                "- <NEXT>PROCESS_RGB</NEXT>"
            ),
        ),
        "PROCESS_SPECTRUM": SkillPhase(
            name="PROCESS_SPECTRUM",
            order=2,
            content=(
                "Solve the task using the available tools.\n"
                "Use exact filenames from `get_filelist` or prior tool outputs.\n"
                "Create intermediate rasters before aggregation when the task requires them.\n"
                "Avoid fabricated file paths and avoid reusing a failed call with the same arguments.\n"
                "Move to CONCLUDE once you have enough evidence.\n\n"
                "Available actions:\n"
                "- <CALL>tool_name</CALL><ARGS>{...}</ARGS>\n"
                "- <NEXT>CONCLUDE</NEXT>"
            ),
        ),
        "PROCESS_PRODUCTS": SkillPhase(
            name="PROCESS_PRODUCTS",
            order=3,
            content=(
                "Solve the task using the available tools.\n"
                "Use exact filenames from `get_filelist` or prior tool outputs.\n"
                "For time-series or arithmetic tasks, compute the needed intermediate values before the final comparison.\n"
                "Move to CONCLUDE once you have enough evidence.\n\n"
                "Available actions:\n"
                "- <CALL>tool_name</CALL><ARGS>{...}</ARGS>\n"
                "- <NEXT>CONCLUDE</NEXT>"
            ),
        ),
        "PROCESS_RGB": SkillPhase(
            name="PROCESS_RGB",
            order=4,
            content=(
                "Solve the task using the available tools.\n"
                "Use the image filenames returned by `get_filelist`.\n"
                "For counting or ranking tasks, gather the counts first. For grounding or geometry tasks, gather the boxes, centroids, or distances first.\n"
                "Move to CONCLUDE once you have enough evidence.\n\n"
                "Available actions:\n"
                "- <CALL>tool_name</CALL><ARGS>{...}</ARGS>\n"
                "- <NEXT>CONCLUDE</NEXT>"
            ),
        ),
        "CONCLUDE": SkillPhase(
            name="CONCLUDE",
            order=5,
            content=(
                "Review the evidence you already collected and choose the best-supported option from the available choices.\n"
                "Prefer an exact evidence match when one is available.\n"
                "Output the final answer as a single choice letter.\n\n"
                "Available actions:\n"
                "- <ANSWER>A</ANSWER>\n"
                "- <ANSWER>B</ANSWER>\n"
                "- <ANSWER>C</ANSWER>\n"
                "- <ANSWER>D</ANSWER>\n"
                "- <ANSWER>E</ANSWER>\n"
                "- <ANSWER>F</ANSWER>\n"
                "- <ANSWER>G</ANSWER>\n"
                "- <ANSWER>H</ANSWER>"
            ),
        ),
    }
    body = "\n\n".join(
        [
            "## Phase: INIT\n\n" + phases["INIT"].content,
            "## Phase: IDENTIFY\n\n" + phases["IDENTIFY"].content,
            "## Phase: PROCESS_SPECTRUM\n\n" + phases["PROCESS_SPECTRUM"].content,
            "## Phase: PROCESS_PRODUCTS\n\n" + phases["PROCESS_PRODUCTS"].content,
            "## Phase: PROCESS_RGB\n\n" + phases["PROCESS_RGB"].content,
            "## Phase: CONCLUDE\n\n" + phases["CONCLUDE"].content,
        ]
    )
    header = SkillHeader(
        name="no-skill-bare-generic",
        description="Minimal generic executor scaffold with no task-specific skill guidance.",
        skill_dir=str(skill_dir),
        skill_md_path=str(skill_dir / "SKILL.md"),
        compatibility="generic-no-skill-baseline",
        allowed_tools=allowed_tools,
        metadata={"evaluation_mode": EVALUATION_MODE},
    )
    full_text = (
        "---\n"
        "name: no-skill-bare-generic\n"
        "description: Minimal generic executor scaffold with no task-specific skill guidance.\n"
        "metadata:\n"
        f"  evaluation_mode: {EVALUATION_MODE}\n"
        "---\n\n"
        f"{body}\n"
    )
    (skill_dir / "SKILL.md").write_text(full_text, encoding="utf-8")
    return SkillDetail(
        header=header,
        body=body,
        phases=phases,
        resources=[],
        full_text=full_text,
    )


def _metric_summary(results: list[dict]) -> dict:
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
    summary: dict[str, object] = {
        "count": len(results),
        "success_count": sum(1 for item in results if item["metrics"].get("task_success")),
        "evaluation_mode": EVALUATION_MODE,
    }
    for key in metric_keys:
        values = [float(item["metrics"].get(key, 0.0)) for item in results]
        summary[f"avg_{key}"] = round(sum(values) / len(values), 4) if values else 0.0
    summary["named_avg_metrics"] = {
        name: summary[f"avg_{metric}"] for name, metric in aliases.items()
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a no-skill executor baseline on benchmark tasks.")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG), help="Path to local system config.")
    parser.add_argument("--run-name", type=str, default=None, help="Optional dated run name.")
    parser.add_argument("--question-file", type=str, help="File with one question ID per line.")
    parser.add_argument("--question", type=str, help="Single question ID.")
    parser.add_argument("--start", type=int, help="Start question ID.")
    parser.add_argument("--end", type=int, help="End question ID.")
    parser.add_argument("--all", action="store_true", help="Evaluate all questions.")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent task workers.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not any([args.question_file, args.question, args.start is not None, args.all]):
        parser.error("one of --question-file / --question / --start / --all is required")

    config = load_system_config(args.config)
    run_name = args.run_name or f"no_skill_gpt52_batch30_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = prepare_dated_run_dir(config.run_root, run_name=run_name)
    log_path = setup_logging(run_dir, args.verbose)

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
    allowed_tools = sorted(
        {
            tool_name
            for task in tasks
            for tool_name in task.gold_tool_names
            if tool_name
        }
        | {"get_filelist", "read_file", "run_python_script"}
    )
    no_skill = _build_no_skill_detail(run_dir, allowed_tools)
    write_json(run_dir / "run_metadata.json", {"evaluation_mode": EVALUATION_MODE, "run_dir": str(run_dir)})
    write_json(
        run_dir / "pipeline_context.json",
        {
            "config_path": str(Path(args.config).resolve()),
            "question_file": str(Path(args.question_file).resolve()) if args.question_file else "",
            "question_ids": question_ids,
            "task_count": len(tasks),
            "evaluation_mode": EVALUATION_MODE,
            "executor_model": config.executor.model,
            "executor_base_url": config.executor.base_url,
            "executor_api_mode": config.executor.api_mode,
            "executor_timeout_seconds": config.executor.timeout_seconds,
            "eval_concurrency": args.concurrency,
            "prompt_root": str(config.prompt_root),
            "allowed_tools": allowed_tools,
            "notes": "Minimal generic phases only. No batch-trained skill rules were injected.",
        },
    )
    write_json(
        run_dir / "config_snapshot.json",
        {
            "config": to_dict(config),
            "no_skill_detail": {
                "header": to_dict(no_skill.header),
                "phases": {name: to_dict(phase) for name, phase in no_skill.phases.items()},
            },
        },
    )

    log.info("Run dir: %s", run_dir)
    log.info("Evaluation mode: %s", EVALUATION_MODE)
    log.info("Executor model: %s", config.executor.model)
    log.info("Executor endpoint: %s", config.executor.base_url)
    log.info("Selected %d tasks: %s", len(tasks), ", ".join(question_ids))

    primary_env = SkillEnvironment(config)
    shared_eo_runtime = primary_env.toolbox.context.eo_runtime

    total = len(tasks)
    results: list[dict | None] = [None] * total
    concurrency = max(1, min(args.concurrency, total))

    def _run_eval_task(idx: int, task: DatasetTask) -> dict:
        env = SkillEnvironment(config, shared_eo_runtime=shared_eo_runtime)
        qid = str(task.metadata.get("original_question_id", task.task_id))
        task_dir = ensure_dir(run_dir / f"task_{idx:02d}_{qid}")
        write_json(task_dir / "task.json", to_dict(task))
        log.info("[%d/%d] Running Q%s", idx, total, qid)
        try:
            state = env.run(task, no_skill, task_dir / "env")
            evaluation = state.env_result.evaluation
            metrics = {
                "accuracy": evaluation.accuracy,
                "efficiency": evaluation.efficiency,
                "tool_any_order": evaluation.tool_any_order,
                "tool_in_order": evaluation.tool_in_order,
                "tool_exact_match": evaluation.tool_exact_match,
                "parameter_accuracy": evaluation.parameter_accuracy,
                "task_success": evaluation.task_success,
                "notes": evaluation.notes,
            }
            result_entry = {
                "task_id": task.task_id,
                "original_question_id": qid,
                "evaluation_mode": EVALUATION_MODE,
                "selected_skill": "",
                "has_applicable_skill": False,
                "router_notes": "no-skill-executor-bypass",
                "metrics": metrics,
                "final_answer": state.env_result.final_answer,
                "final_choice_label": state.env_result.final_choice_label,
                "executor_summary": state.env_result.executor_summary,
            }
            write_json(task_dir / "evaluation_summary.json", result_entry)
            log.info(
                "[%d/%d] Q%s | success=%s | TAO=%.4f | TIO=%.4f | TEM=%.4f | ACC=%.4f",
                idx,
                total,
                qid,
                evaluation.task_success,
                evaluation.tool_any_order,
                evaluation.tool_in_order,
                evaluation.tool_exact_match,
                evaluation.accuracy,
            )
            return result_entry
        except Exception as exc:
            failure = {
                "task_id": task.task_id,
                "original_question_id": qid,
                "evaluation_mode": EVALUATION_MODE,
                "selected_skill": "",
                "has_applicable_skill": False,
                "router_notes": "no-skill-executor-bypass",
                "metrics": {
                    "accuracy": 0.0,
                    "efficiency": 0.0,
                    "tool_any_order": 0.0,
                    "tool_in_order": 0.0,
                    "tool_exact_match": 0.0,
                    "parameter_accuracy": 0.0,
                    "task_success": False,
                    "notes": "Executor failure.",
                    "error": str(exc),
                },
                "final_answer": "",
                "final_choice_label": "",
                "error": str(exc),
            }
            write_json(task_dir / "evaluation_failure.json", failure)
            write_json(task_dir / "evaluation_summary.json", failure)
            log.error("[%d/%d] Q%s executor failed: %s", idx, total, qid, exc, exc_info=True)
            return failure

    log.info("Launching %d concurrent evaluation workers", concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx = {
            pool.submit(_run_eval_task, idx, task): idx - 1
            for idx, task in enumerate(tasks, 1)
        }
        with tqdm(total=total, desc="Evaluating", unit="task") as pbar:
            for future in as_completed(future_to_idx):
                slot = future_to_idx[future]
                results[slot] = future.result()
                pbar.update(1)

    results = [item for item in results if item is not None]
    summary = _metric_summary(results)
    summary.update(
        {
            "run_dir": str(run_dir),
            "executor_model": config.executor.model,
            "executor_base_url": config.executor.base_url,
            "executor_api_mode": config.executor.api_mode,
            "question_ids": question_ids,
        }
    )
    write_json(run_dir / "records.json", results)
    write_json(run_dir / "evaluation_summary.json", summary)
    log.info("Batch summary: %s", json.dumps(summary["named_avg_metrics"], ensure_ascii=False))
    print(f"Logs written to {log_path}")
    print(f"Run dir: {run_dir}")


if __name__ == "__main__":
    main()
