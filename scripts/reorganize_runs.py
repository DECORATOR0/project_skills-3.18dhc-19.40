#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlrl_skills.utils import dated_run_parent, ensure_dir

SHARED_DIR_NAMES = {"temp", "logs", "cache", "nohup_logs", "_launch_logs"}
YEAR_DIR_RE = re.compile(r"20\d{2}")


def discover_run_roots(project_root: Path) -> list[Path]:
    return sorted(
        path
        for path in project_root.iterdir()
        if path.is_dir() and (path.name == "runs" or path.name.startswith("runs_"))
    )


def is_already_grouped(entry: Path) -> bool:
    return bool(YEAR_DIR_RE.fullmatch(entry.name)) or entry.name == "_shared"


def classify_entry(root: Path, entry: Path) -> str:
    if root.name == "runs" and (entry.name in SHARED_DIR_NAMES or is_already_grouped(entry)):
        return "skip"
    if root.name != "runs" and entry.name in SHARED_DIR_NAMES:
        return "shared"
    return "run"


def planned_destination(runs_root: Path, source_root: Path, entry: Path) -> Path | None:
    if source_root.name != "runs" and entry.is_file():
        return runs_root / "_shared" / source_root.name / entry.name
    category = classify_entry(source_root, entry)
    if category == "skip":
        return None
    if category == "shared":
        return runs_root / "_shared" / source_root.name / entry.name
    return dated_run_parent(runs_root, run_name=entry.name, path=entry) / entry.name


def plan_moves(project_root: Path) -> list[tuple[Path, Path]]:
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        raise FileNotFoundError(f"Missing runs root: {runs_root}")

    moves: list[tuple[Path, Path]] = []
    seen_destinations: dict[Path, Path] = {}
    for source_root in discover_run_roots(project_root):
        for entry in sorted(source_root.iterdir(), key=lambda item: item.name):
            if not entry.is_dir() and not (source_root.name != "runs" and entry.is_file()):
                continue
            destination = planned_destination(runs_root, source_root, entry)
            if destination is None:
                continue
            existing_source = seen_destinations.get(destination)
            if existing_source is not None and existing_source != entry:
                raise FileExistsError(f"Multiple sources map to the same destination: {existing_source} and {entry} -> {destination}")
            if destination.exists() and destination.resolve() != entry.resolve():
                raise FileExistsError(f"Destination already exists: {destination}")
            seen_destinations[destination] = entry
            moves.append((entry, destination))
    return moves


def apply_moves(moves: list[tuple[Path, Path]]) -> None:
    for source, destination in moves:
        ensure_dir(destination.parent)
        shutil.move(str(source), str(destination))


def cleanup_empty_legacy_roots(project_root: Path) -> list[Path]:
    removed: list[Path] = []
    for path in discover_run_roots(project_root):
        if path.name == "runs":
            continue
        if any(path.iterdir()):
            continue
        path.rmdir()
        removed.append(path)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reorganize legacy runs* directories into runs/year/month/day folders.")
    parser.add_argument("--project-root", default=Path(__file__).resolve().parents[1], type=Path, help="Repository root")
    parser.add_argument("--apply", action="store_true", help="Apply the planned moves. Without this flag the script only prints the plan.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    moves = plan_moves(project_root)

    for source, destination in moves:
        print(f"{source} -> {destination}")
    print(f"\nPlanned moves: {len(moves)}")

    if not args.apply:
        return

    apply_moves(moves)
    removed_roots = cleanup_empty_legacy_roots(project_root)
    print(f"Removed empty legacy roots: {len(removed_roots)}")
    for path in removed_roots:
        print(path)


if __name__ == "__main__":
    main()
