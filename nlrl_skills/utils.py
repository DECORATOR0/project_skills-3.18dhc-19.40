from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RUN_DATE_TOKEN_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")


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


def resolve_run_calendar_parts(
    *,
    run_name: str | None = None,
    path: Path | None = None,
    dt: datetime | None = None,
) -> tuple[str, str, str]:
    if run_name:
        match = _RUN_DATE_TOKEN_RE.search(run_name)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            return str(year), str(month), f"{year}-{month}-{day}"

    reference_dt = dt
    if reference_dt is None and path is not None:
        reference_dt = datetime.fromtimestamp(path.stat().st_mtime)
    if reference_dt is None:
        reference_dt = datetime.now()
    return (
        str(reference_dt.year),
        str(reference_dt.month),
        f"{reference_dt.year}-{reference_dt.month}-{reference_dt.day}",
    )


def dated_run_parent(
    run_root: Path,
    *,
    run_name: str | None = None,
    path: Path | None = None,
    dt: datetime | None = None,
) -> Path:
    year, month, day_label = resolve_run_calendar_parts(run_name=run_name, path=path, dt=dt)
    return run_root / year / month / day_label


def prepare_dated_run_dir(
    run_root: Path,
    *,
    run_name: str | None = None,
    dt: datetime | None = None,
) -> Path:
    resolved_run_name = run_name or f"task_local_parallel_batch_{utc_timestamp()}"
    run_dir = dated_run_parent(run_root, run_name=resolved_run_name, dt=dt) / resolved_run_name
    return ensure_empty_dir(run_dir)


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


def _strip_model_json_wrappers(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"(?is)<think>.*?</think>\s*", "", cleaned).strip()
    fence_match = re.search(r"(?is)^```(?:json)?\s*(.*?)\s*```$", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return cleaned


def _escape_json_control_chars(text: str) -> str:
    escaped: list[str] = []
    in_string = False
    pending_escape = False
    replacement_map = {
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }

    for ch in text:
        if in_string:
            if pending_escape:
                escaped.append(ch)
                pending_escape = False
                continue
            if ch == "\\":
                escaped.append(ch)
                pending_escape = True
                continue
            if ch == '"':
                escaped.append(ch)
                in_string = False
                continue
            if ord(ch) < 0x20:
                escaped.append(replacement_map.get(ch, f"\\u{ord(ch):04x}"))
                continue
            escaped.append(ch)
            continue

        escaped.append(ch)
        if ch == '"':
            in_string = True

    return "".join(escaped)


def _iter_json_object_candidates(text: str):
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        pending_escape = False
        for end in range(start, len(text)):
            current = text[end]
            if in_string:
                if pending_escape:
                    pending_escape = False
                    continue
                if current == "\\":
                    pending_escape = True
                    continue
                if current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
                continue
            if current == "{":
                depth += 1
                continue
            if current == "}":
                depth -= 1
                if depth == 0:
                    yield start, text[start : end + 1]
                    break


def _load_json_dict(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")
    return data


_PREFERRED_TOP_LEVEL_JSON_KEYS = {
    "action",
    "tool_name",
    "arguments",
    "final_answer",
    "choice_label",
    "summary",
    "natural_language_reward",
    "reward_dimensions",
    "experience_note",
    "action_type",
    "target_skill_name",
    "files_to_write",
    "files_to_delete",
    "experience_entry",
    "skill",
}


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_model_json_wrappers(text)
    if not cleaned:
        raise ValueError("Empty model response; expected JSON object.")

    attempts = [cleaned]
    sanitized = _escape_json_control_chars(cleaned)
    if sanitized != cleaned:
        attempts.append(sanitized)

    last_error: Exception | None = None
    for candidate in attempts:
        try:
            return _load_json_dict(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc

    valid_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    seen_candidates: set[tuple[int, str]] = set()
    for source in attempts:
        for start, candidate in _iter_json_object_candidates(source):
            marker = (start, candidate)
            if marker in seen_candidates:
                continue
            seen_candidates.add(marker)
            for variant in (candidate, _escape_json_control_chars(candidate)):
                try:
                    payload = _load_json_dict(variant)
                    preferred_key_hits = len(set(payload) & _PREFERRED_TOP_LEVEL_JSON_KEYS)
                    if preferred_key_hits or start == 0:
                        valid_candidates.append((preferred_key_hits, -start, len(candidate), payload))
                        break
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
    if valid_candidates:
        _, _, _, payload = max(valid_candidates)
        return payload

    if "{" not in cleaned or "}" not in cleaned:
        raise ValueError(f"Unable to locate JSON object in response: {cleaned[:400]}")
    raise ValueError(str(last_error) if last_error is not None else "Unable to parse model response as a JSON object.")


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
