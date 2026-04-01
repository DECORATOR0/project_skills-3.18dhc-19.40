"""
Load Earth-Bench questions for skill_eval without depending on planner.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import BENCHMARK_PATH, DATA_DIR_CANDIDATES, PROJECT_ROOT
from .schemas import BenchmarkItem


def _resolve_data_dir(logical_path: str) -> str:
    suffix = Path(logical_path).name
    for base in DATA_DIR_CANDIDATES:
        candidate = base / suffix
        if candidate.exists():
            return str(candidate)
    full = PROJECT_ROOT / logical_path
    if full.exists():
        return str(full)
    return logical_path


def _extract_gold_tools(dialogs: list[dict]) -> tuple[list[str], list[dict]]:
    tool_names: list[str] = []
    tool_calls: list[dict] = []
    for turn in dialogs:
        if turn.get("role") == "assistant" and "tool_calls" in turn:
            for tc in turn["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                if name:
                    tool_names.append(name)
                    tool_calls.append(fn)
    return tool_names, tool_calls


def _extract_file_list_from_dialogs(dialogs: list[dict]) -> list[str]:
    for turn in dialogs:
        if turn.get("role") == "tool" and turn.get("name") == "get_filelist":
            content = turn.get("content", {})
            files = content.get("content", [])
            if isinstance(files, list):
                return [str(f) for f in files]
    return []


def load_benchmark(json_path=None, question_ids: Optional[list[str]] = None) -> list[BenchmarkItem]:
    path = Path(json_path) if json_path else BENCHMARK_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw: dict = json.load(f)

    items: list[BenchmarkItem] = []
    for qid, qdata in raw.items():
        if question_ids and qid not in question_ids:
            continue

        evals = qdata.get("evaluation", [])
        ap_eval = None
        data_dir = ""
        for ev in evals:
            t = ev.get("type", "").strip().lower()
            if t == "autonomous planning":
                ap_eval = ev
            if ev.get("data"):
                data_dir = ev["data"]

        if ap_eval is None:
            continue

        question_text = ap_eval.get("question", "")
        raw_gt = ap_eval.get("gt_answer", {}).get("whitelist", "")
        gt_answer = ",".join(raw_gt) if isinstance(raw_gt, list) else str(raw_gt or "")
        resolved = _resolve_data_dir(data_dir) if data_dir else ""

        dialogs = qdata.get("dialogs", [])
        gold_names, gold_calls = _extract_gold_tools(dialogs)
        file_list = _extract_file_list_from_dialogs(dialogs)

        items.append(
            BenchmarkItem(
                question_id=qid,
                question_text=question_text,
                data_dir=resolved,
                file_list=file_list,
                gold_tool_names=gold_names,
                gold_tool_calls=gold_calls,
                choices=qdata.get("choices") or [],
                gt_answer=gt_answer,
            )
        )

    items.sort(key=lambda x: int(x.question_id))
    return items
