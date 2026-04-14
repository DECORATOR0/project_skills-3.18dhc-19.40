#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
from datetime import datetime
from pathlib import Path

FINAL_STATUSES = {
    "finished",
    "stopped",
    "stopped_partial",
    "dead",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_datetime(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed
        except ValueError:
            pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%a %b %d %H:%M:%S %Y",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed
        except ValueError:
            continue
    return None


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return fieldnames, rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_log_tail(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def infer_terminal_status(row: dict[str, str]) -> str:
    existing = (row.get("status") or "").strip()
    if existing in FINAL_STATUSES:
        return existing

    run_dir = Path((row.get("run_dir") or "").strip()) if (row.get("run_dir") or "").strip() else None
    if run_dir:
        if (run_dir / "run_summary.json").exists():
            return "finished"
        if (run_dir / "evaluation_summary.json").exists():
            return "finished"

    log_path = Path((row.get("log_path") or "").strip()) if (row.get("log_path") or "").strip() else None
    log_tail = read_log_tail(log_path) if log_path else ""
    if "train_exit status=0" in log_tail:
        return "finished"
    if existing.startswith("stopped"):
        return existing
    return "dead"


def get_process_info(pid: int) -> tuple[str, str] | None:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout.strip()
    if proc.returncode != 0 or not output:
        return None
    tokens = output.split()
    if len(tokens) < 6:
        return None
    stat = tokens[-1]
    start_text = " ".join(tokens[:-1])
    return start_text, stat


def process_matches_row(row: dict[str, str], process_start: str) -> bool:
    recorded_start = parse_datetime(row.get("start_time", ""))
    actual_start = parse_datetime(process_start)
    if recorded_start is None or actual_start is None:
        return True
    return abs((recorded_start - actual_start).total_seconds()) <= 5


def refresh_registry(path: Path) -> list[str]:
    fieldnames, rows = read_rows(path)
    checked_at = now_iso()
    updates: list[str] = []

    for row in rows:
        row["last_checked_at"] = checked_at
        pid_raw = (row.get("pid") or "").strip()
        if not pid_raw.isdigit():
            updates.append(f"{row.get('name', '')}: no_pid -> {row.get('status', '') or '(blank)'}")
            continue

        pid = int(pid_raw)
        process_info = get_process_info(pid)
        if process_info is not None and process_matches_row(row, process_info[0]):
            previous = (row.get("status") or "").strip()
            row["status"] = "running"
            if previous != "running":
                updates.append(f"{row.get('name', '')}: {previous or '(blank)'} -> running")
            else:
                updates.append(f"{row.get('name', '')}: running")
            continue

        previous = (row.get("status") or "").strip()
        terminal_status = infer_terminal_status(row)
        row["status"] = terminal_status
        if not (row.get("ended_at") or "").strip():
            row["ended_at"] = checked_at
        updates.append(f"{row.get('name', '')}: {previous or '(blank)'} -> {terminal_status}")

    write_rows(path, fieldnames, rows)
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh /data/xsy/活的进程.csv by checking each recorded PID.")
    parser.add_argument(
        "--csv-path",
        default="/data/xsy/活的进程.csv",
        help="Path to the shared process registry CSV.",
    )
    args = parser.parse_args()

    path = Path(args.csv_path)
    updates = refresh_registry(path)
    print(f"refreshed={len(updates)} csv={path}")
    for line in updates:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
