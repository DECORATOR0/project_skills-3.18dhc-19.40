from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_empty_dir(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"Directory already exists and is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> Path:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, data: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
    return path


def append_jsonl(path: Path, row: dict[str, Any]) -> Path:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "skill"


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Empty model response; expected JSON object.")
    text = re.sub(r"(?is)<think>.*?</think>\s*", "", text).strip()
    fence_match = re.search(r"(?is)^```(?:json)?\s*(.*?)\s*```$", text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Top-level JSON must be an object.")
        return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Unable to locate JSON object in response: {text[:400]}")
    candidate = text[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")
    return data


def safe_relative_path(base_dir: Path, user_path: str) -> Path:
    candidate = (base_dir / user_path).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved == candidate or base_resolved in candidate.parents:
        return candidate
    raise ValueError(f"Path escapes base directory: {user_path}")


def relative_to(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")
