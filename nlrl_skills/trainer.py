from __future__ import annotations

from pathlib import Path

from .actor import SkillActor
from .config import SystemConfig
from .critic import SkillCritic
from .data import load_converted_dataset, select_task, select_tasks
from .environment import SkillEnvironment
from .schemas import DatasetTask, TrainIterationRecord, to_dict
from .skills import discover_skills
from .utils import ensure_dir, ensure_empty_dir, utc_timestamp, write_json


class SkillRLTrainer:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.environment = SkillEnvironment(config)
        self.critic = SkillCritic(config)
        self.actor = SkillActor(config)

    def prepare_run_dir(self, run_name: str | None = None) -> Path:
        run_dir = self.config.run_root / (run_name or f"run_{utc_timestamp()}")
        ensure_empty_dir(run_dir)
        write_json(run_dir / "config_snapshot.json", to_dict(self.config))
        return run_dir

    def train_task(self, task: DatasetTask, task_run_dir: Path) -> dict:
        write_json(task_run_dir / "task.json", task.__dict__)
        iteration_records: list[dict] = []
        task_success = False

        for iteration_index in range(1, self.config.runtime.max_iterations_per_task + 1):
            iteration_dir = ensure_dir(task_run_dir / f"iteration_{iteration_index:02d}")
            skill_headers = discover_skills(self.config.skill_library_root)
            write_json(iteration_dir / "skill_headers_before.json", [header.__dict__ for header in skill_headers])
            try:
                state = self.environment.run(task, skill_headers, iteration_dir / "env")
                reward = self.critic.evaluate(state, iteration_dir / "critic")
            except Exception as exc:
                write_json(
                    iteration_dir / "iteration_failure.json",
                    {
                        "iteration_index": iteration_index,
                        "error": str(exc),
                    },
                )
                iteration_records.append(
                    {
                        "iteration_index": iteration_index,
                        "error": str(exc),
                    }
                )
                break
            record = TrainIterationRecord(iteration_index=iteration_index, state=state, reward=reward)

            if state.env_result.evaluation.task_success:
                task_success = True
                write_json(
                    iteration_dir / "iteration_summary.json",
                    {
                        "iteration_index": iteration_index,
                        "status": "task_success",
                        "evaluation": state.env_result.evaluation.__dict__,
                        "reward": reward.__dict__,
                    },
                )
                iteration_records.append(
                    {
                        "iteration_index": iteration_index,
                        "evaluation": state.env_result.evaluation.__dict__,
                        "reward": reward.__dict__,
                        "actor_decision": None,
                    }
                )
                break

            try:
                decision = self.actor.act(state, reward, iteration_dir / "actor")
                self.actor.apply(decision)
            except Exception as exc:
                write_json(
                    iteration_dir / "iteration_failure.json",
                    {
                        "iteration_index": iteration_index,
                        "error": str(exc),
                    },
                )
                iteration_records.append(
                    {
                        "iteration_index": iteration_index,
                        "evaluation": state.env_result.evaluation.__dict__,
                        "reward": reward.__dict__,
                        "error": str(exc),
                    }
                )
                break
            record.actor_decision = decision
            write_json(iteration_dir / "skill_headers_after.json", [header.__dict__ for header in discover_skills(self.config.skill_library_root)])
            write_json(
                iteration_dir / "iteration_summary.json",
                {
                    "iteration_index": iteration_index,
                    "evaluation": state.env_result.evaluation.__dict__,
                    "reward": reward.__dict__,
                    "actor_decision": decision.__dict__ | {"experience_entry": None if decision.experience_entry is None else decision.experience_entry.__dict__},
                },
            )
            iteration_records.append(
                {
                    "iteration_index": iteration_index,
                    "evaluation": state.env_result.evaluation.__dict__,
                    "reward": reward.__dict__,
                    "actor_decision": decision.__dict__ | {"experience_entry": None if decision.experience_entry is None else decision.experience_entry.__dict__},
                }
            )

        task_summary = {
            "task_id": task.task_id,
            "original_question_id": task.metadata.get("original_question_id", ""),
            "task_success": task_success,
            "iterations": iteration_records,
            "final_skill_headers": [header.__dict__ for header in discover_skills(self.config.skill_library_root)],
        }
        write_json(task_run_dir / "task_summary.json", task_summary)
        return task_summary

    def train_single_task(self, task_id: str | None = None, *, run_name: str | None = None) -> Path:
        tasks = load_converted_dataset(self.config.converted_dataset_path)
        task = select_task(tasks, task_id)
        run_dir = self.prepare_run_dir(run_name)
        task_summary = self.train_task(task, run_dir)
        write_json(run_dir / "run_summary.json", task_summary)
        return run_dir

    def train_tasks(
        self,
        *,
        task_ids: list[str] | None = None,
        count: int | None = None,
        start_index: int = 0,
        run_name: str | None = None,
    ) -> Path:
        tasks = load_converted_dataset(self.config.converted_dataset_path)
        selected_tasks = select_tasks(tasks, task_ids=task_ids, count=count, start_index=start_index)
        run_dir = self.prepare_run_dir(run_name)
        write_json(
            run_dir / "selected_tasks.json",
            [
                {
                    "task_id": task.task_id,
                    "original_question_id": task.metadata.get("original_question_id", ""),
                    "prompt": task.prompt,
                }
                for task in selected_tasks
            ],
        )
        task_summaries: list[dict] = []
        for index, task in enumerate(selected_tasks, start=1):
            task_run_dir = ensure_dir(run_dir / f"task_{index:02d}_{task.metadata.get('original_question_id', task.task_id)}")
            task_summaries.append(self.train_task(task, task_run_dir))
        run_summary = {
            "task_count": len(selected_tasks),
            "tasks": task_summaries,
            "final_skill_headers": [header.__dict__ for header in discover_skills(self.config.skill_library_root)],
        }
        write_json(run_dir / "run_summary.json", run_summary)
        return run_dir
