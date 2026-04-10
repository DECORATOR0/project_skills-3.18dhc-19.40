from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import DatasetTask
from .utils import ensure_dir, read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def load_converted_dataset(path: Path) -> list[DatasetTask]:
    raw = read_json(path)
    tasks = raw.get("tasks", [])
    resolved_tasks: list[DatasetTask] = []
    for task in tasks:
        item = DatasetTask(**task)
        item.data_dir = _resolve_task_data_dir(item.data_dir)
        resolved_tasks.append(item)
    return resolved_tasks


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
