from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_system_config
from .data import convert_earth_bench_question_file
from .evaluator_runner import SkillPolicyEvaluator
from .skills import discover_skills, reset_experience_buffer, reset_skill_library
from .trainer import SkillRLTrainer
from .utils import ensure_dir, write_json


def _task_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-ids", nargs="+", help="Normalized task ids or original question ids.")
    parser.add_argument("--count", type=int, help="Take the first N tasks from the dataset slice.")
    parser.add_argument("--start-index", type=int, default=0, help="Zero-based dataset offset before applying count.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Natural-language RL framework for agent skill training.")
    parser.add_argument("--config", required=True, help="Path to configs/system.json")
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert-earth-bench", help="Convert benchmark/question.json into the RL dataset schema.")
    convert.add_argument("--src", required=True, help="Source Earth-Bench question.json")
    convert.add_argument("--dst", required=True, help="Destination normalized question.json")

    inspect = sub.add_parser("inspect-skills", help="Inspect current generated skill headers.")
    inspect.add_argument("--output", help="Optional JSON output path")

    train = sub.add_parser("debug-single-task", help="Run the full loop on one task for debugging.")
    train.add_argument("--task-id", help="Normalized task id or original question id")
    train.add_argument("--run-name", help="Optional run folder name")
    train.add_argument("--reset-skill-library", action="store_true", help="Remove all generated skills before the run.")
    train.add_argument("--reset-experience-buffer", action="store_true", help="Clear the NL-Experience Buffer before the run.")

    train_many = sub.add_parser("train-tasks", help="Train continuously on multiple tasks.")
    _task_selection_args(train_many)
    train_many.add_argument("--run-name", help="Optional run folder name")
    train_many.add_argument("--reset-skill-library", action="store_true", help="Remove all generated skills before the run.")
    train_many.add_argument("--reset-experience-buffer", action="store_true", help="Clear the NL-Experience Buffer before the run.")

    evaluate = sub.add_parser("evaluate-tasks", help="Evaluate the current skill library without further training.")
    _task_selection_args(evaluate)
    evaluate.add_argument("--run-name", help="Optional run folder name")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_system_config(args.config)

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

    if args.command == "debug-single-task":
        if args.reset_skill_library:
            reset_skill_library(config.skill_library_root)
        if args.reset_experience_buffer:
            reset_experience_buffer(config.experience_buffer_path)
        trainer = SkillRLTrainer(config)
        run_dir = trainer.train_single_task(task_id=args.task_id, run_name=args.run_name)
        print(run_dir)
        return

    if args.command == "train-tasks":
        if args.reset_skill_library:
            reset_skill_library(config.skill_library_root)
        if args.reset_experience_buffer:
            reset_experience_buffer(config.experience_buffer_path)
        trainer = SkillRLTrainer(config)
        run_dir = trainer.train_tasks(
            task_ids=args.task_ids,
            count=args.count,
            start_index=args.start_index,
            run_name=args.run_name,
        )
        print(run_dir)
        return

    if args.command == "evaluate-tasks":
        evaluator = SkillPolicyEvaluator(config)
        run_dir = evaluator.evaluate_tasks(
            task_ids=args.task_ids,
            count=args.count,
            start_index=args.start_index,
            run_name=args.run_name,
        )
        print(run_dir)
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
