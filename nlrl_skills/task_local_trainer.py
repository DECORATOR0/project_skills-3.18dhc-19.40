from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
from pathlib import Path

from .actor import SkillActor
from .config import SystemConfig, clone_system_config
from .critic import SkillCritic
from .data import load_converted_dataset, select_tasks
from .environment import SkillEnvironment
from .schemas import ActorDecision, DatasetTask, to_dict
from .skills import discover_skills, reset_experience_buffer, reset_skill_library
from .task_buckets import classify_task_bucket
from .utils import ensure_dir, ensure_empty_dir, slugify, utc_timestamp, write_json


class TaskLocalParallelTrainer:
    def __init__(self, config: SystemConfig):
        self.config = config

    @staticmethod
    def _metric_aliases() -> dict[str, str]:
        return {
            "TAO": "tool_any_order",
            "TIO": "tool_in_order",
            "TEM": "tool_exact_match",
            "Efficiency": "efficiency",
            "Parameters": "parameter_accuracy",
            "Accuracy": "accuracy",
        }

    def _summarize_training_metrics(self, task_summaries: list[dict]) -> dict:
        aliases = self._metric_aliases()
        terminal_metrics = [
            item.get("terminal_metrics", {})
            for item in task_summaries
        ]
        return {
            "task_count": len(task_summaries),
            "success_count": sum(1 for item in task_summaries if item.get("task_success")),
            "success_rate": round(
                sum(1 for item in task_summaries if item.get("task_success")) / len(task_summaries),
                4,
            )
            if task_summaries
            else 0.0,
            "avg_metrics": {
                metric: round(sum(float(metrics.get(metric, 0.0)) for metrics in terminal_metrics) / len(terminal_metrics), 4)
                if terminal_metrics
                else 0.0
                for metric in aliases.values()
            },
            "named_avg_metrics": {
                name: round(sum(float(metrics.get(metric, 0.0)) for metrics in terminal_metrics) / len(terminal_metrics), 4)
                if terminal_metrics
                else 0.0
                for name, metric in aliases.items()
            },
        }

    def prepare_run_dir(self, run_name: str | None = None) -> Path:
        run_dir = self.config.run_root / (run_name or f"task_local_parallel_{utc_timestamp()}")
        ensure_empty_dir(run_dir)
        write_json(
            run_dir / "config_snapshot.json",
            {
                "mode": "task-local-parallel-single-skill",
                "base_config": to_dict(self.config),
            },
        )
        return run_dir

    def _task_dir(self, run_dir: Path, index: int, task: DatasetTask) -> Path:
        original_qid = str(task.metadata.get("original_question_id", task.task_id))
        return ensure_dir(run_dir / f"task_{index:02d}_{original_qid}")

    def _task_local_config(self, task_dir: Path) -> SystemConfig:
        local_root = ensure_dir(task_dir / "task_local_state")
        return clone_system_config(
            self.config,
            paths={
                "run_root": local_root / "runs",
                "skill_library_root": local_root / "skill_library",
                "experience_buffer_path": local_root / "experience_buffer.jsonl",
            },
        )

    def _enforce_single_skill_workspace(self, skill_library_root: Path, keep_skill_name: str) -> None:
        keep_dir_name = slugify(keep_skill_name)
        for child in skill_library_root.iterdir():
            if child.is_dir() and child.name != keep_dir_name:
                shutil.rmtree(child)

    def _normalize_decision(
        self,
        decision: ActorDecision,
        *,
        existing_skill_name: str | None,
    ) -> ActorDecision:
        decision.files_to_delete = []
        decision.merged_from = []
        if existing_skill_name:
            decision.action_type = "modify_skill"
            decision.target_skill_name = existing_skill_name
            if decision.experience_entry is not None:
                decision.experience_entry.action_type = "modify_skill"
                decision.experience_entry.affected_skills = [existing_skill_name]
        else:
            decision.action_type = "create_skill"
            if decision.experience_entry is not None and decision.target_skill_name:
                decision.experience_entry.action_type = "create_skill"
                decision.experience_entry.affected_skills = [decision.target_skill_name]
        return decision

    def _train_single_task_local(self, task: DatasetTask, task_dir: Path) -> dict:
        local_config = self._task_local_config(task_dir)
        reset_skill_library(local_config.skill_library_root)
        reset_experience_buffer(local_config.experience_buffer_path)
        environment = SkillEnvironment(local_config)
        critic = SkillCritic(local_config)
        actor = SkillActor(local_config)
        bucket_info = classify_task_bucket(task)

        write_json(task_dir / "task.json", task.__dict__)
        write_json(task_dir / "task_bucket.json", bucket_info.__dict__)
        write_json(task_dir / "task_local_config_snapshot.json", to_dict(local_config))

        iteration_records: list[dict] = []
        task_success = False
        discard_reason = "iteration_limit_reached"
        last_skill_name = ""
        terminal_metrics = {
            "accuracy": 0.0,
            "efficiency": 0.0,
            "tool_any_order": 0.0,
            "tool_in_order": 0.0,
            "tool_exact_match": 0.0,
            "parameter_accuracy": 0.0,
            "task_success": False,
        }

        for iteration_index in range(1, local_config.runtime.max_iterations_per_task + 1):
            iteration_dir = ensure_dir(task_dir / f"iteration_{iteration_index:02d}")
            skill_headers = discover_skills(local_config.skill_library_root)
            write_json(iteration_dir / "skill_headers_before.json", [header.__dict__ for header in skill_headers])

            forced_skill_name = skill_headers[0].name if skill_headers else None
            try:
                state = environment.run(
                    task,
                    skill_headers,
                    iteration_dir / "env",
                    forced_active_skill_name=forced_skill_name,
                )
                reward = critic.evaluate(state, iteration_dir / "critic")
            except Exception as exc:
                discard_reason = f"iteration_{iteration_index}_env_or_critic_error"
                failure = {
                    "iteration_index": iteration_index,
                    "error": str(exc),
                }
                write_json(iteration_dir / "iteration_failure.json", failure)
                iteration_records.append(failure)
                break

            record = {
                "iteration_index": iteration_index,
                "evaluation": state.env_result.evaluation.__dict__,
                "reward": reward.__dict__,
            }
            terminal_metrics = state.env_result.evaluation.__dict__

            if state.env_result.evaluation.task_success:
                task_success = True
                discard_reason = ""
                write_json(
                    iteration_dir / "iteration_summary.json",
                    {
                        **record,
                        "status": "task_success",
                        "actor_decision": None,
                    },
                )
                iteration_records.append({**record, "actor_decision": None})
                break

            existing_skill_name = state.active_skill.header.name if state.active_skill is not None else None
            forced_action_type = "create_skill" if existing_skill_name is None else "modify_skill"
            try:
                decision = actor.act(
                    state,
                    reward,
                    iteration_dir / "actor",
                    forced_action_type=forced_action_type,
                )
                decision = self._normalize_decision(decision, existing_skill_name=existing_skill_name)
                actor.apply(decision)
                self._enforce_single_skill_workspace(local_config.skill_library_root, decision.target_skill_name)
                last_skill_name = decision.target_skill_name
            except Exception as exc:
                discard_reason = f"iteration_{iteration_index}_actor_error"
                failure = {
                    **record,
                    "error": str(exc),
                }
                write_json(iteration_dir / "iteration_failure.json", failure)
                iteration_records.append(failure)
                break

            current_headers = discover_skills(local_config.skill_library_root)
            write_json(iteration_dir / "skill_headers_after.json", [header.__dict__ for header in current_headers])
            actor_payload = decision.__dict__ | {
                "experience_entry": None if decision.experience_entry is None else decision.experience_entry.__dict__
            }
            write_json(
                iteration_dir / "iteration_summary.json",
                {
                    **record,
                    "actor_decision": actor_payload,
                    "forced_action_type": forced_action_type,
                    "forced_active_skill_name": forced_skill_name,
                },
            )
            iteration_records.append(
                {
                    **record,
                    "actor_decision": actor_payload,
                    "forced_action_type": forced_action_type,
                    "forced_active_skill_name": forced_skill_name,
                }
            )

        final_headers = discover_skills(local_config.skill_library_root)
        final_skill_dir = ""
        if final_headers:
            final_skill_dir = final_headers[0].skill_dir
            last_skill_name = final_headers[0].name

        summary = {
            "task_id": task.task_id,
            "original_question_id": str(task.metadata.get("original_question_id", "")),
            "runtime_family": bucket_info.runtime_family,
            "modality": bucket_info.modality,
            "training_bucket": bucket_info.bucket,
            "task_success": task_success,
            "retained": bool(task_success and final_skill_dir),
            "discard_reason": discard_reason,
            "iterations": iteration_records,
            "terminal_metrics": terminal_metrics,
            "final_skill_name": last_skill_name,
            "final_skill_dir": final_skill_dir,
            "local_skill_library_root": str(local_config.skill_library_root),
            "local_experience_buffer_path": str(local_config.experience_buffer_path),
            "final_skill_headers": [header.__dict__ for header in final_headers],
        }
        write_json(task_dir / "task_summary.json", summary)
        return summary

    def train_tasks(
        self,
        *,
        task_ids: list[str] | None = None,
        count: int | None = None,
        start_index: int = 0,
        concurrency: int = 2,
        run_name: str | None = None,
    ) -> Path:
        tasks = load_converted_dataset(self.config.converted_dataset_path)
        selected_tasks = select_tasks(tasks, task_ids=task_ids, count=count, start_index=start_index)
        run_dir = self.prepare_run_dir(run_name)

        selected_payload = []
        task_dirs: list[tuple[DatasetTask, Path]] = []
        for index, task in enumerate(selected_tasks, start=1):
            bucket_info = classify_task_bucket(task)
            task_dir = self._task_dir(run_dir, index, task)
            task_dirs.append((task, task_dir))
            selected_payload.append(
                {
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "runtime_family": bucket_info.runtime_family,
                    "modality": bucket_info.modality,
                    "training_bucket": bucket_info.bucket,
                    "file_count": len(task.file_list),
                    "prompt": task.prompt,
                }
            )
        write_json(run_dir / "selected_tasks.json", selected_payload)

        task_summaries: list[dict] = []
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            future_map = {
                executor.submit(self._train_single_task_local, task, task_dir): (task, task_dir)
                for task, task_dir in task_dirs
            }
            for future in as_completed(future_map):
                task, task_dir = future_map[future]
                try:
                    task_summaries.append(future.result())
                except Exception as exc:
                    fallback_summary = {
                        "task_id": task.task_id,
                        "original_question_id": str(task.metadata.get("original_question_id", "")),
                        "runtime_family": classify_task_bucket(task).runtime_family,
                        "modality": classify_task_bucket(task).modality,
                        "training_bucket": classify_task_bucket(task).bucket,
                        "task_success": False,
                        "retained": False,
                        "discard_reason": "fatal_thread_error",
                        "error": str(exc),
                        "iterations": [],
                        "final_skill_name": "",
                        "final_skill_dir": "",
                        "final_skill_headers": [],
                    }
                    write_json(task_dir / "task_summary.json", fallback_summary)
                    task_summaries.append(fallback_summary)

        task_summaries.sort(key=lambda item: int(item["original_question_id"]) if item["original_question_id"].isdigit() else item["original_question_id"])
        run_summary = {
            "mode": "task-local-parallel-single-skill",
            "task_count": len(task_summaries),
            "retained_count": sum(1 for item in task_summaries if item.get("retained")),
            "success_count": sum(1 for item in task_summaries if item.get("task_success")),
            "training_metric_basis": "Each task contributes the terminal evaluation from its final training iteration; tasks that succeed early stop immediately and contribute that success iteration.",
            "training_metrics": self._summarize_training_metrics(task_summaries),
            "tasks": task_summaries,
        }
        write_json(run_dir / "run_summary.json", run_summary)
        write_json(
            run_dir / "retained_task_skills.json",
            [item for item in task_summaries if item.get("retained")],
        )
        return run_dir
