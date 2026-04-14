from __future__ import annotations

import shutil
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .actor import SkillActor
from .config import SystemConfig, clone_system_config
from .critic import SkillCritic
from .data import ensure_required_gold_overrides_loaded, load_converted_dataset, select_task, select_tasks
from .environment import SkillEnvironment
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import DatasetTask, LLMMessage, SkillDetail, to_dict
from .skills import discover_skills, load_skill_detail, parse_skill_phases, reset_experience_buffer, reset_skill_library, write_skill_bundle
from .task_buckets import build_task_set_manifest, classify_task_bucket
from .utils import append_jsonl, ensure_dir, prepare_dated_run_dir, slugify, utc_timestamp, write_json


class TaskLocalParallelTrainer:
    DEFAULT_BATCH_SEED = 20260403
    DEFAULT_MODALITY_BATCH_SIZE = 10
    MODALITY_ORDER = ("spectrum", "products", "rgb")

    def __init__(self, config: SystemConfig):
        self.config = config

    def _modality_batch_size(self) -> int:
        size = int(getattr(self.config.runtime, "modality_batch_size", self.DEFAULT_MODALITY_BATCH_SIZE))
        if size <= 0:
            raise ValueError("runtime.modality_batch_size must be >= 1")
        return size

    def _total_batch_size(self) -> int:
        return self._modality_batch_size() * len(self.MODALITY_ORDER)

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
        terminal_metrics = [item.get("terminal_metrics", {}) for item in task_summaries]
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
        run_dir = prepare_dated_run_dir(self.config.run_root, run_name=run_name)
        write_json(
            run_dir / "config_snapshot.json",
            {
                "mode": "single-skill-batch-rl",
                "base_config": to_dict(self.config),
                "batch_policy": {
                    "seed": self.DEFAULT_BATCH_SEED,
                    "modalities": {modality: self._modality_batch_size() for modality in self.MODALITY_ORDER},
                    "batch_size": self._total_batch_size(),
                },
            },
        )
        return run_dir

    def _build_logger(self, run_dir: Path) -> logging.Logger:
        logger = logging.getLogger(f"nlrl_skills.task_local_batch.{run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        return logger

    def _log_event(self, run_dir: Path, event_type: str, **payload: Any) -> None:
        append_jsonl(
            run_dir / "events.jsonl",
            {
                "timestamp": utc_timestamp(),
                "event_type": event_type,
                **payload,
            },
        )

    def _batch_state_config(self, run_dir: Path) -> SystemConfig:
        state_root = ensure_dir(run_dir / "batch_state")
        return clone_system_config(
            self.config,
            paths={
                "run_root": state_root / "runs",
                "skill_library_root": state_root / "skill_library",
                "experience_buffer_path": state_root / "experience_buffer.jsonl",
            },
        )

    def _selected_task_manifest(self, selected_tasks: list[DatasetTask], *, selection_policy: str, seed: int | None) -> dict:
        modalities: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in selected_tasks:
            bucket_info = classify_task_bucket(task)
            modalities[bucket_info.modality].append(
                {
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "bucket": bucket_info.bucket,
                    "file_count": len(task.file_list),
                    "prompt": task.prompt,
                }
            )
        return {
            "selection_policy": selection_policy,
            "seed": seed,
            "batch_size": len(selected_tasks),
            "modalities": {
                modality: sorted(
                    modalities.get(modality, []),
                    key=lambda item: int(item["original_question_id"]) if str(item["original_question_id"]).isdigit() else item["original_question_id"],
                )
                for modality in self.MODALITY_ORDER
            },
            "all_task_ids": [str(task.metadata.get("original_question_id", task.task_id)) for task in selected_tasks],
        }

    def _validate_batch_shape(self, selected_tasks: list[DatasetTask]) -> None:
        total_batch_size = self._total_batch_size()
        modality_batch_size = self._modality_batch_size()
        if len(selected_tasks) != total_batch_size:
            raise ValueError(
                f"train-task-local-parallel now requires a fixed batch of {total_batch_size} tasks; got {len(selected_tasks)}."
            )
        modality_counts: dict[str, int] = defaultdict(int)
        for task in selected_tasks:
            modality_counts[classify_task_bucket(task).modality] += 1
        for modality in self.MODALITY_ORDER:
            if modality_counts.get(modality, 0) != modality_batch_size:
                raise ValueError(
                    f"train-task-local-parallel requires exactly {modality_batch_size} `{modality}` tasks; "
                    f"got {modality_counts.get(modality, 0)}."
                )

    def _select_batch_tasks(
        self,
        tasks: list[DatasetTask],
        *,
        task_ids: list[str] | None,
        count: int | None,
        start_index: int,
    ) -> tuple[list[DatasetTask], dict]:
        explicit_selection = bool(task_ids) or count is not None or start_index != 0
        if explicit_selection:
            selected_tasks = select_tasks(tasks, task_ids=task_ids, count=count, start_index=start_index)
            self._validate_batch_shape(selected_tasks)
            return selected_tasks, self._selected_task_manifest(
                selected_tasks,
                selection_policy="explicit_fixed_batch",
                seed=None,
            )

        manifest = build_task_set_manifest(tasks, seed=self.DEFAULT_BATCH_SEED)
        selected_tasks = [select_task(tasks, task_id) for task_id in manifest["all_task_ids"]]
        self._validate_batch_shape(selected_tasks)
        return selected_tasks, manifest

    def _task_dir(self, parent_dir: Path, index: int, task: DatasetTask) -> Path:
        original_qid = str(task.metadata.get("original_question_id", task.task_id))
        return ensure_dir(parent_dir / f"task_{index:02d}_{original_qid}")

    def _serialize_tool_chain_truth(self, task: DatasetTask) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for turn in task.gold_trajectory:
            if turn.get("role") != "assistant":
                continue
            for call in turn.get("tool_calls", []):
                function = call.get("function", {})
                arguments: Any = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except Exception:
                        arguments = {"raw": arguments}
                steps.append(
                    {
                        "tool_name": str(function.get("name", "")),
                        "arguments": arguments,
                    }
                )
        return steps

    def _source_skill_context(self) -> list[dict[str, Any]]:
        """Read existing skills from the global library as seed context (may be empty)."""
        payload: list[dict[str, Any]] = []
        root = self.config.skill_library_root
        if not root.exists():
            return payload
        for header in discover_skills(root):
            try:
                detail = load_skill_detail(header)
            except Exception:
                payload.append({"header": header.__dict__})
                continue
            payload.append(
                {
                    "header": header.__dict__,
                    "body": detail.body,
                    "resources": detail.resources,
                }
            )
        return payload

    def _bootstrap_task_payload(self, selected_tasks: list[DatasetTask]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for task in selected_tasks:
            bucket_info = classify_task_bucket(task)
            payload.append(
                {
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "modality": bucket_info.modality,
                    "bucket": bucket_info.bucket,
                    "prompt": task.prompt,
                    "choices": task.choices,
                    "gold_answer": task.gold_answer,
                    "gold_tool_names": task.gold_tool_names,
                    "gold_tool_chain_truth": self._serialize_tool_chain_truth(task),
                    "file_count": len(task.file_list),
                    "file_list_preview": task.file_list[:20],
                }
            )
        return payload

    def _fallback_skill_bundle(self, selected_tasks: list[DatasetTask]) -> dict[str, str]:
        tool_names = sorted({tool_name for task in selected_tasks for tool_name in task.gold_tool_names})
        allowed_tools_block = "".join(f"  - {tool_name}\n" for tool_name in tool_names)
        buckets = sorted({classify_task_bucket(task).bucket for task in selected_tasks})
        qids = [str(task.metadata.get("original_question_id", task.task_id)) for task in selected_tasks[:10]]
        examples = "\n".join(f"- Q{qid}" for qid in qids) or "- (none)"
        skill_md = (
            f"---\n"
            f"name: earth-bench-batch\n"
            f"description: Bootstrap seed skill for the full training batch covering spectrum, products, and rgb tasks.\n"
            f"allowed-tools:\n"
            f"{allowed_tools_block}"
            f"metadata:\n"
            f"  bootstrap_strategy: deterministic_fallback\n"
            f"---\n\n"
            "## When To Use\n\n"
            "- Use this skill for all Earth-Bench questions in the current training batch.\n"
            f"- Covered buckets: {', '.join(buckets) if buckets else '(none)'}.\n"
            "- Preserve benchmark-faithful tool ordering when the task resembles one of the seed examples.\n\n"
            "## In-Batch Examples\n\n"
            f"{examples}\n"
        )
        execution_guidance = (
            "# Batch Skill Execution Guidance\n\n"
            "- Start from the real file list and the answer choices that are present in the current task.\n"
            "- Reuse gold tool-chain patterns from the batch before inventing a new workflow.\n"
            "- If an expected file or intermediate artifact is missing, report the blocker instead of fabricating inputs.\n"
        )
        return {
            "SKILL.md": skill_md,
            "references/EXECUTION_GUIDANCE.md": execution_guidance,
        }

    def _bootstrap_single_skill(
        self,
        *,
        batch_config: SystemConfig,
        selected_tasks: list[DatasetTask],
        run_dir: Path,
        logger: logging.Logger,
    ) -> dict:
        reset_skill_library(batch_config.skill_library_root)
        reset_experience_buffer(batch_config.experience_buffer_path)
        source_skills = self._source_skill_context()
        write_json(run_dir / "source_skill_library_snapshot.json", source_skills)

        bootstrap_dir = ensure_dir(run_dir / "bootstrap")
        batch_task_payload = self._bootstrap_task_payload(selected_tasks)
        context_payload = {
            "current_skill_library": source_skills,
            "batch_tasks": batch_task_payload,
            "requirements": {
                "batch_size": len(selected_tasks),
                "single_skill": True,
                "use_gold_tool_chain_truth": True,
            },
        }
        write_json(bootstrap_dir / "bootstrap_context.json", context_payload)
        self._log_event(run_dir, "bootstrap_start", task_count=len(selected_tasks), source_skill_count=len(source_skills))

        llm = OpenAICompatibleLLM(batch_config.actor)
        system_prompt = render_prompt(batch_config.prompt_root / "bootstrap_batch_skill_system.md")
        user_prompt = render_prompt(
            batch_config.prompt_root / "bootstrap_batch_skill_library.md",
            current_skills_json=json.dumps(source_skills, ensure_ascii=False, indent=2),
            batch_tasks_json=json.dumps(batch_task_payload, ensure_ascii=False, indent=2),
        )
        payload, llm_result = llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(bootstrap_dir, "bootstrap_single_skill", llm_result)

        skill_payload = payload.get("skill", payload)
        skill_name = str(skill_payload.get("target_skill_name", "")).strip()
        if not skill_name:
            raise ValueError("Bootstrap payload must contain `target_skill_name`.")
        raw_files = skill_payload.get("files_to_write", {})
        if not isinstance(raw_files, dict):
            raise ValueError("Bootstrap skill returned invalid `files_to_write`.")
        files_to_write = {str(k): str(v) for k, v in raw_files.items()}
        if "SKILL.md" not in files_to_write:
            raise ValueError("Bootstrap skill must include `SKILL.md`.")
        write_skill_bundle(batch_config.skill_library_root, skill_name, files_to_write)
        summary = {
            "skill_name": skill_name,
            "task_count": len(skill_payload.get("source_task_ids", [])),
            "source_task_ids": [str(item) for item in skill_payload.get("source_task_ids", [])],
            "strategy": "llm_single_skill_bootstrap",
            "summary": str(skill_payload.get("summary", "")),
            "skill_dir": str((batch_config.skill_library_root / skill_name).resolve()),
            "written_files": sorted(files_to_write),
        }

        write_json(bootstrap_dir / "bootstrap_summary.json", summary)
        write_json(run_dir / "bootstrap_summary.json", summary)
        self._log_event(run_dir, "bootstrap_done", skill_name=summary["skill_name"])
        logger.info("Bootstrapped single skill: %s", summary["skill_name"])
        return summary

    def _bootstrap_from_snapshot(
        self,
        *,
        batch_config: SystemConfig,
        snapshot_dir: Path,
        selected_tasks: list,
        run_dir: Path,
        logger: logging.Logger,
    ) -> dict:
        """Copy a pre-existing skill snapshot into the batch skill library instead of running LLM bootstrap."""
        reset_skill_library(batch_config.skill_library_root)
        reset_experience_buffer(batch_config.experience_buffer_path)

        for src_file in snapshot_dir.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(snapshot_dir)
                dst = batch_config.skill_library_root / rel
                ensure_dir(dst.parent)
                shutil.copy2(src_file, dst)

        skill_name = None
        for d in batch_config.skill_library_root.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                skill_name = d.name
                break
        if not skill_name:
            raise RuntimeError(f"No valid skill found in snapshot: {snapshot_dir}")

        summary = {
            "skill_name": skill_name,
            "task_count": len(selected_tasks),
            "source_task_ids": [t.task_id for t in selected_tasks],
            "strategy": "snapshot_copy",
            "summary": f"Copied from snapshot: {snapshot_dir}",
            "skill_dir": str((batch_config.skill_library_root / skill_name).resolve()),
            "written_files": sorted(
                str(f.relative_to(batch_config.skill_library_root / skill_name))
                for f in (batch_config.skill_library_root / skill_name).rglob("*")
                if f.is_file()
            ),
        }
        bootstrap_dir = ensure_dir(run_dir / "bootstrap")
        write_json(bootstrap_dir / "bootstrap_summary.json", summary)
        write_json(run_dir / "bootstrap_summary.json", summary)
        self._log_event(run_dir, "bootstrap_done", skill_name=skill_name, strategy="snapshot_copy")
        logger.info("Bootstrapped from snapshot: %s -> %s", snapshot_dir, skill_name)
        return summary

    def _snapshot_skill_library(self, batch_config: SystemConfig, target_dir: Path, logger: logging.Logger) -> None:
        """Copy the current skill library into *target_dir*/skill_snapshot/ for provenance."""
        snapshot_dir = ensure_dir(target_dir / "skill_snapshot")
        src_root = batch_config.skill_library_root
        if not src_root.exists():
            return
        for src_file in src_root.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(src_root)
                dst = snapshot_dir / rel
                ensure_dir(dst.parent)
                shutil.copy2(src_file, dst)
        logger.info("Saved skill snapshot to %s", snapshot_dir)

    def _load_active_skill(self, batch_config: SystemConfig) -> SkillDetail:
        headers = discover_skills(batch_config.skill_library_root)
        if not headers:
            raise RuntimeError("No skill found in the skill library after bootstrap.")
        header = headers[0]
        detail = load_skill_detail(header)
        if not detail.phases:
            _logger = logging.getLogger("nlrl_skills.trainer")
            _logger.warning(
                "Skill '%s' has no ## Phase: headers — default phases will be used.",
                detail.header.name,
            )
        return detail

    def _run_batch_env(
        self,
        *,
        selected_tasks: list[DatasetTask],
        concurrency: int,
        active_skill: SkillDetail,
        batch_config: SystemConfig,
        shared_eo_runtime,
        iteration_dir: Path,
        logger: logging.Logger,
        run_dir: Path,
        iteration_index: int,
    ) -> list[dict]:
        event_lock = threading.Lock()

        def _log_event_safe(event_type: str, **payload: Any) -> None:
            with event_lock:
                self._log_event(run_dir, event_type, **payload)

        def _run_single_task(task_index: int, task: DatasetTask) -> dict:
            env = SkillEnvironment(batch_config, shared_eo_runtime=shared_eo_runtime)
            task_dir = self._task_dir(iteration_dir, task_index, task)
            write_json(task_dir / "task.json", task.__dict__)
            bucket_info = classify_task_bucket(task)
            write_json(task_dir / "task_bucket.json", bucket_info.__dict__)

            qid = task.metadata.get("original_question_id", task.task_id)
            logger.info(
                "Iter %02d | Q%s | modality=%s | skill=%s | start",
                iteration_index, qid, bucket_info.modality, active_skill.header.name,
            )
            try:
                state = env.run(task, active_skill, task_dir / "env")
                evaluation = state.env_result.evaluation.__dict__
                record: dict = {
                    "iteration_index": iteration_index,
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "modality": bucket_info.modality,
                    "training_bucket": bucket_info.bucket,
                    "task_success": bool(state.env_result.evaluation.task_success),
                    "evaluation": evaluation,
                    "terminal_metrics": evaluation,
                    "active_skill_name": active_skill.header.name,
                    "state": state,
                }
                logger.info(
                    "Iter %02d | Q%s | success=%s | TAO=%.4f | ACC=%.4f",
                    iteration_index, qid, record["task_success"],
                    evaluation.get("tool_any_order", 0.0), evaluation.get("accuracy", 0.0),
                )
            except Exception as exc:
                logger.error("Iter %02d | Q%s | executor LLM failed (skipping task): %s", iteration_index, qid, exc, exc_info=True)
                record = {
                    "iteration_index": iteration_index,
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "modality": bucket_info.modality,
                    "training_bucket": bucket_info.bucket,
                    "task_success": False,
                    "evaluation": {},
                    "terminal_metrics": {
                        "accuracy": 0.0, "efficiency": 0.0,
                        "tool_any_order": 0.0, "tool_in_order": 0.0,
                        "tool_exact_match": 0.0, "parameter_accuracy": 0.0,
                    },
                    "active_skill_name": active_skill.header.name,
                    "state": None,
                    "error": str(exc),
                }
            write_json(task_dir / "task_record.json", {k: to_dict(v) if k == "state" else v for k, v in record.items()})
            _log_event_safe(
                "task_done",
                iteration_index=iteration_index,
                task_id=task.task_id,
                task_success=record["task_success"],
                terminal_metrics=record["terminal_metrics"],
            )
            return record

        task_count = len(selected_tasks)
        max_workers = max(1, min(concurrency, task_count))
        records: list[dict | None] = [None] * task_count
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_run_single_task, task_index, task): task_index - 1
                for task_index, task in enumerate(selected_tasks, start=1)
            }
            desc = f"Iter {iteration_index:02d} Executor ({task_count} tasks, workers={max_workers})"
            with tqdm(total=task_count, desc=desc, unit="task") as pbar:
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        records[idx] = future.result()
                    except Exception as exc:
                        task = selected_tasks[idx]
                        bucket_info = classify_task_bucket(task)
                        logger.error("Iter %02d | task worker %d crashed: %s", iteration_index, idx + 1, exc)
                        records[idx] = {
                            "iteration_index": iteration_index,
                            "task_id": task.task_id,
                            "original_question_id": str(task.metadata.get("original_question_id", "")),
                            "modality": bucket_info.modality,
                            "training_bucket": bucket_info.bucket,
                            "task_success": False,
                            "evaluation": {},
                            "terminal_metrics": {
                                "accuracy": 0.0, "efficiency": 0.0,
                                "tool_any_order": 0.0, "tool_in_order": 0.0,
                                "tool_exact_match": 0.0, "parameter_accuracy": 0.0,
                            },
                            "active_skill_name": active_skill.header.name,
                            "state": None,
                            "error": str(exc),
                        }
                    done = sum(1 for r in records if r is not None)
                    success = sum(1 for r in records if r is not None and r.get("task_success"))
                    pbar.update(1)
                    pbar.set_postfix(done=done, success=success, refresh=True)

        return [r for r in records if r is not None]

    def train_tasks(
        self,
        *,
        task_ids: list[str] | None = None,
        count: int | None = None,
        start_index: int = 0,
        concurrency: int = 2,
        run_name: str | None = None,
        bootstrap_snapshot: Path | None = None,
    ) -> Path:
        tasks = load_converted_dataset(self.config.converted_dataset_path, self.config.gold_overrides_path)
        selected_tasks, manifest = self._select_batch_tasks(
            tasks, task_ids=task_ids, count=count, start_index=start_index,
        )
        ensure_required_gold_overrides_loaded(
            selected_tasks,
            workspace_root=self.config.workspace_root,
            active_override_path=self.config.gold_overrides_path,
        )
        run_dir = self.prepare_run_dir(run_name)
        logger = self._build_logger(run_dir)
        batch_config = self._batch_state_config(run_dir)

        logger.info("Prepared batch run at %s", run_dir)
        logger.info("Selected %d tasks. Single-skill batch RL with k=%d iterations.", len(selected_tasks), batch_config.runtime.iterations_per_batch)
        logger.info("Executor worker concurrency capped at %d.", max(1, concurrency))
        self._log_event(run_dir, "run_start", run_directory=str(run_dir), batch_size=len(selected_tasks))

        selected_payload = []
        for task in selected_tasks:
            bucket_info = classify_task_bucket(task)
            selected_payload.append({
                "task_id": task.task_id,
                "original_question_id": str(task.metadata.get("original_question_id", "")),
                "modality": bucket_info.modality,
                "training_bucket": bucket_info.bucket,
                "file_count": len(task.file_list),
                "prompt": task.prompt,
                "gold_tool_names": task.gold_tool_names,
            })
        write_json(run_dir / "selected_tasks.json", selected_payload)
        write_json(run_dir / "task_set_manifest.json", manifest)
        write_json(run_dir / "batch_state_config_snapshot.json", to_dict(batch_config))

        if bootstrap_snapshot and bootstrap_snapshot.exists():
            bootstrap_summary = self._bootstrap_from_snapshot(
                batch_config=batch_config,
                snapshot_dir=bootstrap_snapshot,
                selected_tasks=selected_tasks,
                run_dir=run_dir,
                logger=logger,
            )
        else:
            bootstrap_summary = self._bootstrap_single_skill(
                batch_config=batch_config,
                selected_tasks=selected_tasks,
                run_dir=run_dir,
                logger=logger,
            )
        logger.info("Completed bootstrap: skill=%s", bootstrap_summary["skill_name"])
        self._snapshot_skill_library(batch_config, ensure_dir(run_dir / "iteration_00"), logger)

        primary_env = SkillEnvironment(batch_config)
        shared_eo_runtime = primary_env.toolbox.context.eo_runtime
        critic = SkillCritic(batch_config)
        actor = SkillActor(batch_config)

        history_poll: list[dict] = []
        iteration_summaries: list[dict] = []
        k = batch_config.runtime.iterations_per_batch

        for iteration_index in tqdm(range(1, k + 1), desc="RL iterations", unit="iter"):
            logger.info("=" * 60)
            logger.info("Starting iteration %02d/%02d", iteration_index, k)
            self._log_event(run_dir, "iteration_start", iteration_index=iteration_index, k=k)
            iteration_dir = ensure_dir(run_dir / f"iteration_{iteration_index:02d}")

            self._snapshot_skill_library(batch_config, iteration_dir, logger)
            active_skill = self._load_active_skill(batch_config)
            write_json(iteration_dir / "active_skill.json", {
                **active_skill.header.__dict__,
                "phases": sorted(active_skill.phases.keys()),
            })

            records = self._run_batch_env(
                selected_tasks=selected_tasks,
                concurrency=concurrency,
                active_skill=active_skill,
                batch_config=batch_config,
                shared_eo_runtime=shared_eo_runtime,
                iteration_dir=iteration_dir,
                logger=logger,
                run_dir=run_dir,
                iteration_index=iteration_index,
            )

            env_states = [r["state"] for r in records if r.get("state") is not None]
            success_count = sum(1 for r in records if r.get("task_success"))
            metrics_summary = self._summarize_training_metrics(records)
            logger.info(
                "Iter %02d | Executor done: %d/%d succeeded",
                iteration_index, success_count, len(records),
            )

            iteration_skipped = False
            reward = None
            actor_payload: dict | None = None

            try:
                logger.info("Iter %02d | Running critic ...", iteration_index)
                reward = critic.evaluate_batch(
                    env_states, active_skill, history_poll,
                    ensure_dir(iteration_dir / "critic"),
                )
                logger.info("Iter %02d | Critic done: %s", iteration_index, reward.summary[:120])
            except Exception as exc:
                logger.error(
                    "Iter %02d | Critic LLM call failed after all retries: %s  — skipping this iteration.",
                    iteration_index, exc, exc_info=True,
                )
                self._log_event(run_dir, "iteration_skipped", iteration_index=iteration_index, reason=f"critic_failed: {exc}")
                iteration_skipped = True

            if not iteration_skipped and reward is not None:
                try:
                    logger.info("Iter %02d | Running actor ...", iteration_index)
                    decision = actor.act(
                        reward, active_skill, history_poll,
                        ensure_dir(iteration_dir / "actor"),
                    )
                    actor.apply(decision)
                    actor_payload = decision.__dict__ | {
                        "experience_entry": None if decision.experience_entry is None else decision.experience_entry.__dict__,
                    }
                    logger.info("Iter %02d | Actor done: %s", iteration_index, decision.summary[:120])
                except Exception as exc:
                    logger.error(
                        "Iter %02d | Actor LLM call failed after all retries: %s  — skipping this iteration.",
                        iteration_index, exc, exc_info=True,
                    )
                    self._log_event(run_dir, "iteration_skipped", iteration_index=iteration_index, reason=f"actor_failed: {exc}")
                    iteration_skipped = True

            if not iteration_skipped and reward is not None:
                history_entry = {
                    "iteration": iteration_index,
                    "problems": reward.summary,
                    "reward_dimensions": reward.reward_dimensions,
                    "experience_note": reward.experience_note,
                    "success_count": success_count,
                    "total_count": len(records),
                }
                history_poll.append(history_entry)

            iteration_summary = {
                "iteration_index": iteration_index,
                "task_count": len(records),
                "success_count": success_count,
                "success_rate": round(success_count / len(records), 4) if records else 0.0,
                "training_metrics": metrics_summary,
                "reward_summary": reward.summary if reward else "N/A (critic failed)",
                "actor_decision": actor_payload if actor_payload else {"skipped": iteration_skipped},
                "skipped": iteration_skipped,
            }
            write_json(iteration_dir / "iteration_summary.json", iteration_summary)
            iteration_summaries.append(iteration_summary)

            logger.info(
                "Finished iteration %02d | success=%d/%d | skipped=%s",
                iteration_index, success_count, len(records), iteration_skipped,
            )
            self._log_event(
                run_dir, "iteration_done",
                iteration_index=iteration_index,
                success_count=success_count,
                task_count=len(records),
                skipped=iteration_skipped,
            )

            if not iteration_skipped and success_count == len(records):
                logger.info("All tasks succeeded in iteration %02d; stopping early.", iteration_index)
                self._log_event(run_dir, "run_early_stop", iteration_index=iteration_index, reason="all_tasks_succeeded")
                break

        write_json(run_dir / "history_poll.json", history_poll)

        final_skill = self._load_active_skill(batch_config)
        run_summary = {
            "mode": "single-skill-batch-rl",
            "batch_size": len(selected_tasks),
            "iterations_completed": len(iteration_summaries),
            "iterations_per_batch": k,
            "final_success_count": iteration_summaries[-1]["success_count"] if iteration_summaries else 0,
            "training_metrics": self._summarize_training_metrics(
                [r for s in iteration_summaries for r in s.get("training_metrics", {}).get("tasks", [])]
                if any("tasks" in s.get("training_metrics", {}) for s in iteration_summaries)
                else []
            ) if iteration_summaries else {},
            "bootstrap_summary": bootstrap_summary,
            "final_skill_library_root": str(batch_config.skill_library_root),
            "final_experience_buffer_path": str(batch_config.experience_buffer_path),
            "final_skill_name": final_skill.header.name,
            "iteration_summaries": iteration_summaries,
            "history_poll": history_poll,
        }
        write_json(run_dir / "run_summary.json", run_summary)
        self._log_event(
            run_dir, "run_done",
            iterations_completed=len(iteration_summaries),
            final_skill_name=final_skill.header.name,
        )
        logger.info(
            "Training complete. %d iterations. Final skill: %s. Artifacts at: %s",
            len(iteration_summaries),
            final_skill.header.name,
            batch_config.skill_library_root,
        )
        return run_dir
