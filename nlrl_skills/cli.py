from __future__ import annotations

import argparse
from pathlib import Path

from .config import clone_system_config, load_system_config
from .data import convert_earth_bench_question_file
from .skills import discover_skills
from .task_buckets import build_task_set_manifest, export_task_set_manifest
from .task_local_trainer import TaskLocalParallelTrainer
from .utils import read_text
from .utils import write_json


def _task_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-ids", nargs="+", help="Normalized task ids or original question ids.")
    parser.add_argument("--task-file", help="Optional newline-delimited file of normalized task ids or original question ids.")
    parser.add_argument("--count", type=int, help="Take the first N tasks from the dataset slice.")
    parser.add_argument("--start-index", type=int, default=0, help="Zero-based dataset offset before applying count.")


def _resolve_task_ids(args) -> list[str] | None:
    if getattr(args, "task_file", None):
        return [line.strip() for line in read_text(Path(args.task_file)).splitlines() if line.strip()]
    return args.task_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Natural-language RL framework for agent skill training.")
    parser.add_argument("--config", required=True, help="Path to configs/system.json")
    parser.add_argument("--skill-library-root", help="Optional override for the active skill library root.")
    parser.add_argument("--experience-buffer-path", help="Optional override for the active NL-Experience Buffer path.")
    parser.add_argument("--run-root", help="Optional override for the output run root.")
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert-earth-bench", help="Convert benchmark/question.json into the RL dataset schema.")
    convert.add_argument("--src", required=True, help="Source Earth-Bench question.json")
    convert.add_argument("--dst", required=True, help="Destination normalized question.json")

    inspect = sub.add_parser("inspect-skills", help="Inspect current generated skill headers.")
    inspect.add_argument("--output", help="Optional JSON output path")

    sample = sub.add_parser("sample-task-set", help="Create a stratified training set and export concrete task-id files.")
    sample.add_argument("--seed", type=int, default=20260403, help="Fixed random seed used inside the bucket sampler.")
    sample.add_argument("--quota-scale", type=int, default=1, help="Multiply the default per-bucket quotas.")
    sample.add_argument("--output-dir", help="Output directory for generated task-id files and manifest.")

    train_local = sub.add_parser(
        "train-task-local-parallel",
        help="Train a single shared skill over a 30-task batch with k iterations of RL.",
    )
    _task_selection_args(train_local)
    train_local.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Compatibility flag. Batch updates run sequentially on one shared skill.",
    )
    train_local.add_argument("--run-name", help="Optional run folder name")
    train_local.add_argument(
        "--bootstrap-snapshot",
        help="Path to an existing skill snapshot directory (e.g. runs/2026/4/2026-4-11/train_batch_30_v4/iteration_00/skill_snapshot). "
             "If provided, copies this snapshot as the initial skill instead of running LLM bootstrap.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_system_config(args.config)
    path_overrides = {}
    if args.skill_library_root:
        path_overrides["skill_library_root"] = args.skill_library_root
    if args.experience_buffer_path:
        path_overrides["experience_buffer_path"] = args.experience_buffer_path
    if args.run_root:
        path_overrides["run_root"] = args.run_root
    if path_overrides:
        config = clone_system_config(config, paths=path_overrides)

    if args.command == "convert-earth-bench":
        path = convert_earth_bench_question_file(Path(args.src), Path(args.dst))
        print(path)
        return

    if args.command == "inspect-skills":
        headers = [header.__dict__ for header in discover_skills(config.skill_library_root)]
        if args.output:
            write_json(Path(args.output), headers)
        else:
            print(headers)
        return

    if args.command == "sample-task-set":
        from .data import load_converted_dataset

        output_dir = Path(args.output_dir) if args.output_dir else config.workspace_root / "data" / "task_sets" / f"task_local_parallel_seed_{args.seed}"
        manifest = build_task_set_manifest(load_converted_dataset(config.converted_dataset_path), seed=args.seed, quota_scale=args.quota_scale)
        export_task_set_manifest(output_dir, manifest)
        print(output_dir)
        return

    if args.command == "train-task-local-parallel":
        trainer = TaskLocalParallelTrainer(config)
        bootstrap_snapshot = Path(args.bootstrap_snapshot) if args.bootstrap_snapshot else None
        run_dir = trainer.train_tasks(
            task_ids=_resolve_task_ids(args),
            count=args.count,
            start_index=args.start_index,
            concurrency=args.concurrency,
            run_name=args.run_name,
            bootstrap_snapshot=bootstrap_snapshot,
        )
        print(run_dir)
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
