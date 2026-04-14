from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .schemas import DatasetTask
from .utils import ensure_dir, read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TASK_OVERRIDE_FIELDS = {
    "task_id",
    "source_type",
    "prompt",
    "choices",
    "gold_answer",
    "data_dir",
    "file_list",
    "gold_tool_names",
    "gold_trajectory",
}


def _extract_gold_tools(dialogs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for turn in dialogs:
        if turn.get("role") == "assistant":
            for call in turn.get("tool_calls", []):
                fn = call.get("function", {})
                name = fn.get("name")
                if name:
                    names.append(str(name))
    return names


def _extract_file_list(dialogs: list[dict[str, Any]]) -> list[str]:
    for turn in dialogs:
        if turn.get("role") == "tool" and turn.get("name") == "get_filelist":
            content = turn.get("content", {})
            if isinstance(content, dict) and isinstance(content.get("content"), list):
                return [str(item) for item in content["content"]]
    return []


def _resolve_task_data_dir(raw_path: str) -> str:
    path = Path(str(raw_path))
    if path.is_absolute() and path.exists():
        return str(path)

    candidate = (PROJECT_ROOT / path).resolve()
    if candidate.exists():
        return str(candidate)

    parts = path.parts
    if len(parts) >= 2 and parts[0] == "benchmark" and parts[1] in {"data", "Earth-Bench"}:
        pointer_path = PROJECT_ROOT / "benchmark" / parts[1]
        if pointer_path.is_file():
            pointed_root = Path(pointer_path.read_text(encoding="utf-8").strip()).expanduser()
            candidate = pointed_root.joinpath(*parts[2:])
            if candidate.exists():
                return str(candidate)
    return str(raw_path)


def convert_earth_bench_question_file(src_path: Path, dst_path: Path) -> Path:
    raw = read_json(src_path)
    tasks: list[dict[str, Any]] = []
    for qid, record in sorted(raw.items(), key=lambda item: int(item[0])):
        evals = record.get("evaluation", [])
        ap_eval = None
        data_dir = ""
        for item in evals:
            if str(item.get("type", "")).strip().lower() == "autonomous planning":
                ap_eval = item
            if item.get("data"):
                data_dir = str(item["data"])
        if ap_eval is None:
            continue
        dialogs = record.get("dialogs", [])
        task = DatasetTask(
            task_id=f"earth-bench-c-{qid}",
            source_type="C",
            prompt=str(ap_eval.get("question", "")),
            choices=[str(choice) for choice in (record.get("choices") or [])],
            gold_answer=",".join(ap_eval.get("gt_answer", {}).get("whitelist", [])),
            data_dir=data_dir,
            file_list=_extract_file_list(dialogs),
            gold_trajectory=dialogs,
            gold_tool_names=_extract_gold_tools(dialogs),
            original_record=record,
            metadata={
                "original_question_id": qid,
                "evaluation_type": "Autonomous Planning",
                "source_file": str(src_path).replace("\\", "/"),
            },
        )
        tasks.append(
            {
                "task_id": task.task_id,
                "source_type": task.source_type,
                "prompt": task.prompt,
                "choices": task.choices,
                "gold_answer": task.gold_answer,
                "data_dir": task.data_dir,
                "file_list": task.file_list,
                "gold_tool_names": task.gold_tool_names,
                "gold_trajectory": task.gold_trajectory,
                "metadata": task.metadata,
            }
        )
    payload = {
        "dataset_name": "earth-bench-skill-rl",
        "dataset_version": "1.0",
        "task_count": len(tasks),
        "task_schema_version": "nlrl-skill-training-v1",
        "tasks": tasks,
    }
    ensure_dir(dst_path.parent)
    write_json(dst_path, payload)
    return dst_path


def _override_matches_task(task: dict[str, Any], override: dict[str, Any]) -> bool:
    task_id = str(task.get("task_id", ""))
    original_qid = str(task.get("metadata", {}).get("original_question_id", ""))
    override_task_id = str(override.get("task_id", "")).strip()
    override_qid = str(override.get("original_question_id", "")).strip()
    return bool(
        (override_task_id and override_task_id == task_id)
        or (override_qid and override_qid == original_qid)
    )


def _apply_gold_trajectory_patch(
    gold_trajectory: list[dict[str, Any]],
    patch: dict[str, Any],
) -> list[dict[str, Any]]:
    updated = copy.deepcopy(gold_trajectory)
    final_content = patch.get("set_final_assistant_content")
    if final_content is None:
        return updated

    final_index = -1
    for idx, turn in enumerate(updated):
        if turn.get("role") == "assistant" and turn.get("content") is not None:
            final_index = idx

    if final_index >= 0:
        updated[final_index]["content"] = str(final_content)
    else:
        updated.append({"role": "assistant", "content": str(final_content)})
    return updated


def _apply_task_override(task: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    updates = override.get("updates", {})
    if not isinstance(updates, dict):
        raise ValueError("Override `updates` must be a JSON object.")

    updated = copy.deepcopy(task)
    for field in _TASK_OVERRIDE_FIELDS - {"gold_trajectory"}:
        if field in updates:
            updated[field] = copy.deepcopy(updates[field])

    if "gold_trajectory" in updates:
        updated["gold_trajectory"] = copy.deepcopy(updates["gold_trajectory"])
    elif "gold_trajectory_patch" in updates:
        patch = updates["gold_trajectory_patch"]
        if not isinstance(patch, dict):
            raise ValueError("Override `gold_trajectory_patch` must be a JSON object.")
        updated["gold_trajectory"] = _apply_gold_trajectory_patch(updated.get("gold_trajectory", []), patch)

    metadata_patch = updates.get("metadata", {})
    metadata = copy.deepcopy(updated.get("metadata", {}))
    if isinstance(metadata_patch, dict):
        metadata.update(copy.deepcopy(metadata_patch))
    metadata["gold_override_applied"] = True
    if override.get("override_id"):
        metadata["gold_override_id"] = str(override["override_id"])
    if override.get("reason"):
        metadata["gold_override_reason"] = str(override["reason"])
    updated["metadata"] = metadata
    return updated


def apply_gold_overrides_to_dataset(raw: dict[str, Any], override_path: Path | None) -> dict[str, Any]:
    if override_path is None:
        return raw
    if not override_path.exists():
        raise FileNotFoundError(f"Gold override file not found: {override_path}")

    manifest = read_json(override_path)
    overrides = manifest.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError("Gold override manifest must contain a list field named `overrides`.")

    payload = copy.deepcopy(raw)
    applied_override_ids: list[str] = []
    updated_tasks: list[dict[str, Any]] = []

    for task in payload.get("tasks", []):
        matches = [
            override
            for override in overrides
            if override.get("enabled", True) is not False and _override_matches_task(task, override)
        ]
        if len(matches) > 1:
            raise ValueError(
                f"Multiple enabled overrides matched task {task.get('task_id')}: "
                f"{[item.get('override_id', '(no override_id)') for item in matches]}"
            )
        if matches:
            override = matches[0]
            task = _apply_task_override(task, override)
            applied_override_ids.append(str(override.get("override_id", task.get("task_id", ""))))
        updated_tasks.append(task)

    payload["tasks"] = updated_tasks
    payload["gold_override_manifest"] = {
        "path": str(override_path),
        "applied_count": len(applied_override_ids),
        "applied_override_ids": applied_override_ids,
    }
    return payload


def materialize_converted_dataset_with_overrides(
    src_path: Path,
    override_path: Path,
    dst_path: Path,
) -> Path:
    payload = apply_gold_overrides_to_dataset(read_json(src_path), override_path)
    write_json(dst_path, payload)
    return dst_path


def load_converted_dataset(path: Path, override_path: Path | None = None) -> list[DatasetTask]:
    raw = apply_gold_overrides_to_dataset(read_json(path), override_path)
    tasks = raw.get("tasks", [])
    resolved_tasks: list[DatasetTask] = []
    for task in tasks:
        item = DatasetTask(**task)
        item.data_dir = _resolve_task_data_dir(item.data_dir)
        resolved_tasks.append(item)
    return resolved_tasks


def _sorted_qids(values: set[str]) -> list[str]:
    def _key(value: str):
        return (0, int(value)) if value.isdigit() else (1, value)

    return sorted(values, key=_key)


def _discover_enabled_override_targets(workspace_root: Path) -> list[dict[str, Any]]:
    overrides_root = workspace_root / "data" / "gold_overrides"
    if not overrides_root.exists():
        return []

    manifests: list[dict[str, Any]] = []
    for manifest_path in sorted(overrides_root.rglob("*.json")):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        override_items = manifest.get("overrides", [])
        if not isinstance(override_items, list):
            continue

        qids: set[str] = set()
        task_ids: set[str] = set()
        for override in override_items:
            if not isinstance(override, dict):
                continue
            if override.get("enabled", True) is False:
                continue
            qid = str(override.get("original_question_id", "")).strip()
            task_id = str(override.get("task_id", "")).strip()
            if qid:
                qids.add(qid)
            if task_id:
                task_ids.add(task_id)

        if qids or task_ids:
            manifests.append(
                {
                    "path": manifest_path,
                    "question_ids": qids,
                    "task_ids": task_ids,
                }
            )
    return manifests


def ensure_required_gold_overrides_loaded(
    tasks: list[DatasetTask],
    *,
    workspace_root: Path,
    active_override_path: Path | None,
) -> None:
    bypass = os.environ.get("NLRL_ALLOW_MISSING_GOLD_OVERRIDES", "").strip().lower()
    if bypass in {"1", "true", "yes", "on"}:
        return

    manifests = _discover_enabled_override_targets(workspace_root)
    if not manifests:
        return

    selected_qids = {
        str(task.metadata.get("original_question_id", "")).strip()
        for task in tasks
        if str(task.metadata.get("original_question_id", "")).strip()
    }
    selected_task_ids = {task.task_id for task in tasks if task.task_id}

    covered_qids: set[str] = set()
    covered_task_ids: set[str] = set()
    active_path = active_override_path.resolve() if active_override_path is not None else None
    if active_path is not None:
        for manifest in manifests:
            if manifest["path"].resolve() == active_path:
                covered_qids |= manifest["question_ids"]
                covered_task_ids |= manifest["task_ids"]
                break

    remaining_qids = selected_qids - covered_qids
    remaining_task_ids = selected_task_ids - covered_task_ids
    if not remaining_qids and not remaining_task_ids:
        return

    overlaps: list[dict[str, Any]] = []
    for manifest in manifests:
        if active_path is not None and manifest["path"].resolve() == active_path:
            continue
        hit_qids = remaining_qids & manifest["question_ids"]
        hit_task_ids = remaining_task_ids & manifest["task_ids"]
        if hit_qids or hit_task_ids:
            overlaps.append(
                {
                    "path": manifest["path"],
                    "question_ids": _sorted_qids(hit_qids),
                    "task_ids": sorted(hit_task_ids),
                }
            )

    if not overlaps:
        return

    if active_path is None:
        headline = "Selected tasks overlap enabled gold overrides, but no gold override manifest is active."
    else:
        headline = (
            "Selected tasks require gold overrides that are not covered by the currently active manifest: "
            f"{active_path}"
        )

    details: list[str] = []
    for item in overlaps:
        parts = [f"manifest={item['path']}"]
        if item["question_ids"]:
            parts.append(f"question_ids={','.join(item['question_ids'])}")
        if item["task_ids"]:
            parts.append(f"task_ids={','.join(item['task_ids'])}")
        details.append("  - " + " | ".join(parts))

    message = "\n".join(
        [
            headline,
            "Load a config with `paths.gold_overrides_path`, or pass `--gold-overrides-path` explicitly.",
            "If you intentionally need the stale gold labels, rerun with `NLRL_ALLOW_MISSING_GOLD_OVERRIDES=1`.",
            "Matched override manifests:",
            *details,
        ]
    )
    raise SystemExit(message)


def select_task(tasks: list[DatasetTask], task_id: str | None = None) -> DatasetTask:
    if task_id is None:
        return tasks[0]
    for task in tasks:
        if task.task_id == task_id or task.metadata.get("original_question_id") == task_id:
            return task
    raise KeyError(f"Task not found: {task_id}")


def select_tasks(
    tasks: list[DatasetTask],
    *,
    task_ids: list[str] | None = None,
    count: int | None = None,
    start_index: int = 0,
) -> list[DatasetTask]:
    if task_ids:
        return [select_task(tasks, task_id) for task_id in task_ids]
    if start_index < 0:
        raise ValueError("start_index must be >= 0")
    selected = tasks[start_index:]
    if count is not None:
        selected = selected[:count]
    return selected
