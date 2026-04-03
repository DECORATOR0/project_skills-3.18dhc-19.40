from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import SystemConfig
from .data import load_converted_dataset, select_tasks
from .environment import SkillEnvironment
from .schemas import to_dict
from .skills import discover_skills
from .utils import ensure_dir, ensure_empty_dir, utc_timestamp, write_json


class SkillPolicyEvaluator:
    def __init__(self, config: SystemConfig):
        self.config = config

    def _prepare_run_dir(self, run_name: str | None = None) -> Path:
        run_dir = self.config.run_root / (run_name or f"eval_{utc_timestamp()}")
        ensure_empty_dir(run_dir)
        write_json(run_dir / "config_snapshot.json", to_dict(self.config))
        return run_dir

    def _evaluate_single_task(self, task, task_dir: Path) -> dict:
        write_json(task_dir / "task.json", task.__dict__)
        skill_headers = discover_skills(self.config.skill_library_root)
        environment = SkillEnvironment(self.config)
        try:
            state = environment.run(task, skill_headers, task_dir / "env")
            record = {
                "task_id": task.task_id,
                "original_question_id": task.metadata.get("original_question_id", ""),
                "selected_skill": state.router_result.selected_skill,
                "has_applicable_skill": state.router_result.has_applicable_skill,
                "metrics": state.env_result.evaluation.__dict__,
                "final_answer": state.env_result.final_answer,
                "final_choice_label": state.env_result.final_choice_label,
            }
        except Exception as exc:
            record = {
                "task_id": task.task_id,
                "original_question_id": task.metadata.get("original_question_id", ""),
                "error": str(exc),
                "metrics": {
                    "accuracy": 0.0,
                    "efficiency": 0.0,
                    "tool_any_order": 0.0,
                    "tool_in_order": 0.0,
                    "tool_exact_match": 0.0,
                    "parameter_accuracy": 0.0,
                    "task_success": False,
                    "notes": f"Evaluation crashed: {exc}",
                },
            }
            write_json(task_dir / "evaluation_failure.json", record)
        write_json(task_dir / "evaluation_summary.json", record)
        return record

    def evaluate_tasks(
        self,
        *,
        task_ids: list[str] | None = None,
        count: int | None = None,
        start_index: int = 0,
        run_name: str | None = None,
        concurrency: int = 1,
    ) -> Path:
        tasks = load_converted_dataset(self.config.converted_dataset_path)
        selected_tasks = select_tasks(tasks, task_ids=task_ids, count=count, start_index=start_index)
        run_dir = self._prepare_run_dir(run_name)
        per_task_records: list[dict] = []
        task_items = [
            (
                task,
                ensure_dir(run_dir / f"task_{index:02d}_{task.metadata.get('original_question_id', task.task_id)}"),
            )
            for index, task in enumerate(selected_tasks, start=1)
        ]

        if concurrency <= 1:
            for task, task_dir in task_items:
                per_task_records.append(self._evaluate_single_task(task, task_dir))
        else:
            ordered_results: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
                future_map = {
                    executor.submit(self._evaluate_single_task, task, task_dir): (task, task_dir)
                    for task, task_dir in task_items
                }
                for future in as_completed(future_map):
                    task, _task_dir = future_map[future]
                    ordered_results[task.task_id] = future.result()
            for task, _task_dir in task_items:
                per_task_records.append(ordered_results[task.task_id])

        metric_aliases = {
            "TAO": "tool_any_order",
            "TIO": "tool_in_order",
            "TEM": "tool_exact_match",
            "Efficiency": "efficiency",
            "Parameters": "parameter_accuracy",
            "Accuracy": "accuracy",
        }
        summary = {
            "task_count": len(per_task_records),
            "avg_metrics": {
                metric: round(sum(item["metrics"].get(metric, 0.0) for item in per_task_records) / len(per_task_records), 4)
                if per_task_records
                else 0.0
                for metric in metric_aliases.values()
            },
            "named_avg_metrics": {
                name: round(sum(item["metrics"].get(metric, 0.0) for item in per_task_records) / len(per_task_records), 4)
                if per_task_records
                else 0.0
                for name, metric in metric_aliases.items()
            },
            "success_count": sum(1 for item in per_task_records if item["metrics"].get("task_success")),
            "tasks": per_task_records,
        }
        write_json(run_dir / "evaluation_summary.json", summary)
        return run_dir
