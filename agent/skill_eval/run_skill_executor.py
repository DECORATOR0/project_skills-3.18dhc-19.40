"""
Unified evaluation entry point for single-skill batch RL.

Uses the same SkillEnvironment and metric implementation as training,
ensuring train/test parity.
"""
from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from nlrl_skills.config import clone_system_config, load_system_config
from nlrl_skills.data import load_converted_dataset
from nlrl_skills.environment import SkillEnvironment
from nlrl_skills.schemas import to_dict
from nlrl_skills.skills import discover_skills, load_skill_detail
from nlrl_skills.utils import ensure_dir, write_json

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "system.json"


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


def _select_tasks(tasks, args):
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate trained skill on benchmark tasks")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG), help="Path to configs/system.json")
    parser.add_argument("--skill-dir", type=str, help="Path to trained skill library root (overrides config)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", type=str, help="Single question ID to evaluate")
    group.add_argument("--question-file", type=str, help="File with one question ID per line")
    group.add_argument("--start", type=int, help="Start question ID for range")
    group.add_argument("--all", action="store_true", help="Evaluate all questions in the dataset")
    parser.add_argument("--end", type=int, help="End question ID for range (inclusive)")

    parser.add_argument("--concurrency", type=int, default=1, help="Reserved for future use")
    parser.add_argument("--output", type=str, default="agent/skill_eval/execution_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config = load_system_config(args.config)
    if args.skill_dir:
        config = clone_system_config(config, paths={"skill_library_root": args.skill_dir})

    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, args.verbose)

    headers = discover_skills(config.skill_library_root)
    if not headers:
        log.error("No skills found in %s", config.skill_library_root)
        raise SystemExit(1)
    skill = load_skill_detail(headers[0])
    log.info("Loaded skill: %s from %s", skill.header.name, config.skill_library_root)

    tasks = load_converted_dataset(config.converted_dataset_path)
    tasks = _select_tasks(tasks, args)
    if not tasks:
        log.error("No tasks matched the selection criteria.")
        raise SystemExit(1)
    log.info("Evaluating %d tasks with skill=%s", len(tasks), skill.header.name)

    primary_env = SkillEnvironment(config)
    shared_eo_runtime = primary_env.toolbox.context.eo_runtime

    total = len(tasks)

    def _run_eval_task(idx: int, task):
        env = SkillEnvironment(config, shared_eo_runtime=shared_eo_runtime)
        qid = str(task.metadata.get("original_question_id", task.task_id))
        task_dir = ensure_dir(output_dir / f"question_{qid}")
        log.info("[%d/%d] Running Q%s", idx, total, qid)

        try:
            state = env.run(task, skill, task_dir)
            evaluation = state.env_result.evaluation
            metrics = {
                "accuracy": evaluation.accuracy,
                "efficiency": evaluation.efficiency,
                "tool_any_order": evaluation.tool_any_order,
                "tool_in_order": evaluation.tool_in_order,
                "tool_exact_match": evaluation.tool_exact_match,
                "parameter_accuracy": evaluation.parameter_accuracy,
                "task_success": evaluation.task_success,
            }
            log.info(
                "[%d/%d] Q%s | success=%s | TAO=%.4f | TIO=%.4f | TEM=%.4f | ACC=%.4f",
                idx, total, qid, evaluation.task_success,
                evaluation.tool_any_order, evaluation.tool_in_order,
                evaluation.tool_exact_match, evaluation.accuracy,
            )
        except Exception as exc:
            log.error("[%d/%d] Q%s executor LLM failed (skipping): %s", idx, total, qid, exc, exc_info=True)
            metrics = {
                "accuracy": 0.0, "efficiency": 0.0,
                "tool_any_order": 0.0, "tool_in_order": 0.0,
                "tool_exact_match": 0.0, "parameter_accuracy": 0.0,
                "task_success": False, "error": str(exc),
            }

        result_entry = {
            "question_id": qid,
            "task_id": task.task_id,
            "metrics": metrics,
            "skill_name": skill.header.name,
        }
        write_json(task_dir / "eval_result.json", result_entry)
        return result_entry

    results: list[dict | None] = [None] * total
    concurrency = total
    log.info("Launching %d concurrent evaluation workers", concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx = {
            pool.submit(_run_eval_task, idx, task): idx - 1
            for idx, task in enumerate(tasks, 1)
        }
        with tqdm(total=total, desc="Evaluating", unit="task") as pbar:
            for future in as_completed(future_to_idx):
                slot = future_to_idx[future]
                try:
                    results[slot] = future.result()
                except Exception as exc:
                    task = tasks[slot]
                    qid = str(task.metadata.get("original_question_id", task.task_id))
                    log.error("Eval worker for Q%s crashed: %s", qid, exc)
                    results[slot] = {
                        "question_id": qid,
                        "task_id": task.task_id,
                        "metrics": {
                            "accuracy": 0.0, "efficiency": 0.0,
                            "tool_any_order": 0.0, "tool_in_order": 0.0,
                            "tool_exact_match": 0.0, "parameter_accuracy": 0.0,
                            "task_success": False, "error": str(exc),
                        },
                        "skill_name": skill.header.name,
                    }
                pbar.update(1)

    results = [r for r in results if r is not None]

    metric_keys = [
        "accuracy", "efficiency", "tool_any_order",
        "tool_in_order", "tool_exact_match", "parameter_accuracy",
    ]
    summary: dict = {
        "count": len(results),
        "skill_name": skill.header.name,
        "skill_library_root": str(config.skill_library_root),
    }
    for key in metric_keys:
        values = [float(r["metrics"].get(key, 0.0)) for r in results]
        summary[f"avg_{key}"] = round(sum(values) / len(values), 4) if values else 0.0

    aliases = {
        "TAO": "tool_any_order",
        "TIO": "tool_in_order",
        "TEM": "tool_exact_match",
        "Efficiency": "efficiency",
        "Parameters": "parameter_accuracy",
        "Accuracy": "accuracy",
    }
    summary["named_avg_metrics"] = {
        name: summary[f"avg_{metric}"] for name, metric in aliases.items()
    }

    summary_path = write_json(output_dir / "batch_summary.json", summary)
    records_path = write_json(output_dir / "records.json", results)

    log.info("Batch summary: %s", json.dumps(summary["named_avg_metrics"], indent=2))
    print(f"Logs written to {log_path}")
    print(f"Records written to {records_path}")
    print(f"Summary written to {summary_path}")


if __name__ == "__main__":
    main()
