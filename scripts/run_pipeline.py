#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nlrl_skills.config import SystemConfig, clone_system_config, load_system_config
from nlrl_skills.data import load_converted_dataset
from nlrl_skills.evaluator_runner import SkillPolicyEvaluator
from nlrl_skills.skill_aggregator import AggregatedSkillLibraryBuilder
from nlrl_skills.skills import reset_experience_buffer, reset_skill_library
from nlrl_skills.task_buckets import build_task_set_manifest, export_task_set_manifest
from nlrl_skills.task_local_trainer import TaskLocalParallelTrainer
from nlrl_skills.trainer import SkillRLTrainer
from nlrl_skills.utils import ensure_dir, read_json, write_json

CONFIG_ROOT = PROJECT_ROOT / "configs"


def _available_configs() -> dict[str, Path]:
    configs: dict[str, Path] = {}
    for path in sorted(CONFIG_ROOT.glob("*.json")):
        configs[path.name] = path.resolve()
        if path.stem not in configs:
            configs[path.stem] = path.resolve()
    return configs


def _resolve_config_path(value: str) -> Path:
    raw = Path(value)
    if raw.exists():
        return raw.resolve()
    configs = _available_configs()
    if value in configs:
        return configs[value]
    with_suffix = f"{value}.json"
    if with_suffix in configs:
        return configs[with_suffix]
    available = ", ".join(sorted(path.stem for path in CONFIG_ROOT.glob("*.json")))
    raise SystemExit(f"Unknown config `{value}`. Available config aliases: {available}")


def _project_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _project_path_preserve_links(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _runtime_overrides_from_args(args: argparse.Namespace) -> dict[str, int | float | str]:
    runtime: dict[str, int | float | str] = {}
    if getattr(args, "max_iterations", None) is not None:
        runtime["max_iterations_per_task"] = args.max_iterations
    if getattr(args, "max_executor_steps", None) is not None:
        runtime["max_executor_steps"] = args.max_executor_steps
    if getattr(args, "max_actor_steps", None) is not None:
        runtime["max_actor_steps"] = args.max_actor_steps
    if getattr(args, "max_context_chars", None) is not None:
        runtime["max_context_chars"] = args.max_context_chars
    if getattr(args, "skill_count_limit", None) is not None:
        runtime["skill_count_limit"] = args.skill_count_limit
    if getattr(args, "skill_match_threshold", None) is not None:
        runtime["skill_match_threshold"] = args.skill_match_threshold
    return runtime


def _path_overrides_from_args(args: argparse.Namespace) -> dict[str, str]:
    raw = {
        "skill_library_root": getattr(args, "skill_library_root", None),
        "experience_buffer_path": getattr(args, "experience_buffer_path", None),
        "run_root": getattr(args, "run_root", None),
        "dataset_path": getattr(args, "dataset_path", None),
        "converted_dataset_path": getattr(args, "converted_dataset_path", None),
        "docs_root": getattr(args, "docs_root", None),
    }
    return {
        key: str(_project_path(value))
        for key, value in raw.items()
        if value is not None
    }


def _load_config_from_args(args: argparse.Namespace) -> tuple[SystemConfig, Path]:
    config_path = _resolve_config_path(args.config)
    config = load_system_config(config_path)
    path_overrides = _path_overrides_from_args(args)
    runtime_overrides = _runtime_overrides_from_args(args)
    if path_overrides or runtime_overrides:
        config = clone_system_config(config, paths=path_overrides, runtime=runtime_overrides)
    return config, config_path


def _read_task_file(path: str) -> list[str]:
    resolved = _project_path(path)
    assert resolved is not None
    return [line.strip() for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_task_ids(args: argparse.Namespace) -> list[str] | None:
    if getattr(args, "task_file", None):
        return _read_task_file(args.task_file)
    return getattr(args, "task_ids", None)


def _selection_kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "task_ids": _resolve_task_ids(args),
        "count": getattr(args, "count", None),
        "start_index": getattr(args, "start_index", 0),
    }


def _relative_or_abs(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except Exception:
        return str(path.resolve())


def _build_data_report(config: SystemConfig, config_path: Path) -> dict:
    benchmark_json = _project_path_preserve_links(config.paths.dataset_path)
    converted_json = _project_path_preserve_links(config.paths.converted_dataset_path)
    assert benchmark_json is not None
    assert converted_json is not None
    if not benchmark_json.exists():
        raise FileNotFoundError(f"Benchmark question.json not found: {benchmark_json}")
    if not converted_json.exists():
        raise FileNotFoundError(f"Converted dataset not found: {converted_json}")

    benchmark_root = benchmark_json.parent
    data_root = benchmark_root / "data"
    raw = read_json(benchmark_json)
    tasks = load_converted_dataset(converted_json)

    missing_benchmark_dirs: list[dict] = []
    missing_converted_dirs: list[dict] = []
    unexpected_converted_prefixes: list[dict] = []
    referenced_dirs: set[str] = set()

    for qid, record in sorted(raw.items(), key=lambda item: int(str(item[0])) if str(item[0]).isdigit() else str(item[0])):
        for evaluation in record.get("evaluation", []):
            data_dir = str(evaluation.get("data", "")).strip()
            if not data_dir:
                continue
            referenced_dirs.add(data_dir)
            resolved = _project_path(data_dir)
            if resolved is None or not resolved.exists():
                missing_benchmark_dirs.append(
                    {
                        "question_id": str(qid),
                        "data_dir": data_dir,
                        "resolved_path": "" if resolved is None else str(resolved),
                    }
                )

    for task in tasks:
        data_dir = str(task.data_dir).strip()
        resolved = _project_path(data_dir)
        if not data_dir.startswith("benchmark/data/"):
            unexpected_converted_prefixes.append(
                {
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "data_dir": data_dir,
                }
            )
        if resolved is None or not resolved.exists():
            missing_converted_dirs.append(
                {
                    "task_id": task.task_id,
                    "original_question_id": str(task.metadata.get("original_question_id", "")),
                    "data_dir": data_dir,
                    "resolved_path": "" if resolved is None else str(resolved),
                }
            )

    report = {
        "ok": not missing_benchmark_dirs and not missing_converted_dirs and not unexpected_converted_prefixes and data_root.exists(),
        "config": str(config_path),
        "benchmark_question_json": str(benchmark_json),
        "benchmark_question_count": len(raw),
        "benchmark_data_root": str(data_root),
        "benchmark_data_root_exists": data_root.exists(),
        "benchmark_data_root_is_symlink": data_root.is_symlink(),
        "benchmark_data_root_target": str(data_root.resolve()) if data_root.exists() else "",
        "benchmark_referenced_dir_count": len(referenced_dirs),
        "converted_dataset": str(converted_json),
        "converted_task_count": len(tasks),
        "missing_benchmark_dirs": missing_benchmark_dirs,
        "missing_converted_dirs": missing_converted_dirs,
        "converted_unexpected_prefixes": unexpected_converted_prefixes,
    }
    return report


def _print_data_report(report: dict) -> None:
    print(f"config: {report['config']}")
    print(f"benchmark_question_json: {report['benchmark_question_json']}")
    print(f"benchmark_question_count: {report['benchmark_question_count']}")
    print(f"benchmark_data_root: {report['benchmark_data_root']}")
    print(f"benchmark_data_root_exists: {report['benchmark_data_root_exists']}")
    print(f"benchmark_data_root_is_symlink: {report['benchmark_data_root_is_symlink']}")
    if report["benchmark_data_root_target"]:
        print(f"benchmark_data_root_target: {report['benchmark_data_root_target']}")
    print(f"benchmark_referenced_dir_count: {report['benchmark_referenced_dir_count']}")
    print(f"converted_dataset: {report['converted_dataset']}")
    print(f"converted_task_count: {report['converted_task_count']}")
    print(f"missing_benchmark_dirs: {len(report['missing_benchmark_dirs'])}")
    print(f"missing_converted_dirs: {len(report['missing_converted_dirs'])}")
    print(f"converted_unexpected_prefixes: {len(report['converted_unexpected_prefixes'])}")
    print(f"status: {'OK' if report['ok'] else 'FAILED'}")


def cmd_list_configs(args: argparse.Namespace) -> None:
    del args
    seen: set[str] = set()
    for path in sorted(CONFIG_ROOT.glob("*.json")):
        if path.stem in seen:
            continue
        seen.add(path.stem)
        print(f"{path.stem}: {path.relative_to(PROJECT_ROOT)}")


def cmd_check_data(args: argparse.Namespace) -> None:
    config, config_path = _load_config_from_args(args)
    report = _build_data_report(config, config_path)
    _print_data_report(report)
    if args.output:
        output_path = _project_path(args.output)
        assert output_path is not None
        ensure_dir(output_path.parent)
        write_json(output_path, report)
        print(f"report_written: {_relative_or_abs(output_path)}")
    if not report["ok"]:
        raise SystemExit(1)


def cmd_sample(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    output_dir = _project_path(args.output_dir) if args.output_dir else config.workspace_root / "data" / "task_sets" / f"task_local_parallel_seed_{args.seed}_x{args.quota_scale}"
    assert output_dir is not None
    manifest = build_task_set_manifest(
        load_converted_dataset(config.converted_dataset_path),
        seed=args.seed,
        quota_scale=args.quota_scale,
    )
    export_task_set_manifest(output_dir, manifest)
    print(f"output_dir: {_relative_or_abs(output_dir)}")
    print(f"all_task_count: {len(manifest['all_task_ids'])}")
    print(f"smoke_task_count: {len(manifest['smoke_task_ids'])}")


def cmd_train_debug(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    if args.reset_skill_library:
        reset_skill_library(config.skill_library_root)
    if args.reset_experience_buffer:
        reset_experience_buffer(config.experience_buffer_path)
    trainer = SkillRLTrainer(config)
    run_dir = trainer.train_single_task(task_id=args.task_id, run_name=args.run_name)
    print(f"run_dir: {_relative_or_abs(run_dir)}")


def cmd_train_classic(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    if args.reset_skill_library:
        reset_skill_library(config.skill_library_root)
    if args.reset_experience_buffer:
        reset_experience_buffer(config.experience_buffer_path)
    trainer = SkillRLTrainer(config)
    run_dir = trainer.train_tasks(run_name=args.run_name, **_selection_kwargs(args))
    print(f"run_dir: {_relative_or_abs(run_dir)}")


def cmd_train_local(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    trainer = TaskLocalParallelTrainer(config)
    run_dir = trainer.train_tasks(run_name=args.run_name, concurrency=args.concurrency, **_selection_kwargs(args))
    print(f"run_dir: {_relative_or_abs(run_dir)}")


def cmd_aggregate(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    builder = AggregatedSkillLibraryBuilder(config)
    input_run_dir = _project_path(args.input_run_dir)
    output_root = _project_path(args.output_root) if args.output_root else None
    assert input_run_dir is not None
    aggregated_root = builder.build_from_run(input_run_dir, output_root=output_root)
    print(f"output_root: {_relative_or_abs(aggregated_root)}")


def cmd_evaluate(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    evaluator = SkillPolicyEvaluator(config)
    run_dir = evaluator.evaluate_tasks(run_name=args.run_name, concurrency=args.concurrency, **_selection_kwargs(args))
    print(f"run_dir: {_relative_or_abs(run_dir)}")


def cmd_full_local(args: argparse.Namespace) -> None:
    config, _config_path = _load_config_from_args(args)
    trainer = TaskLocalParallelTrainer(config)
    train_run_name = args.train_run_name or (f"{args.run_name}_train" if args.run_name else None)
    local_run_dir = trainer.train_tasks(
        run_name=train_run_name,
        concurrency=args.concurrency,
        **_selection_kwargs(args),
    )

    builder = AggregatedSkillLibraryBuilder(config)
    aggregated_root = builder.build_from_run(
        local_run_dir,
        output_root=_project_path(args.output_root) if args.output_root else None,
    )

    eval_run_name = args.eval_run_name or (f"{args.run_name}_eval" if args.run_name else None)
    eval_config = clone_system_config(
        config,
        paths={"skill_library_root": str(aggregated_root)},
    )
    evaluator = SkillPolicyEvaluator(eval_config)
    eval_run_dir = evaluator.evaluate_tasks(
        run_name=eval_run_name,
        concurrency=args.eval_concurrency or args.concurrency,
        **_selection_kwargs(args),
    )

    summary = {
        "train_run_dir": str(local_run_dir),
        "aggregated_skill_library_root": str(aggregated_root),
        "evaluation_run_dir": str(eval_run_dir),
    }
    write_json(local_run_dir / "pipeline_summary.json", summary)
    print(f"train_run_dir: {_relative_or_abs(local_run_dir)}")
    print(f"aggregated_skill_library_root: {_relative_or_abs(aggregated_root)}")
    print(f"evaluation_run_dir: {_relative_or_abs(eval_run_dir)}")


def cmd_smoke(args: argparse.Namespace) -> None:
    config, config_path = _load_config_from_args(args)
    runtime_defaults = {}
    if args.max_iterations is None:
        runtime_defaults["max_iterations_per_task"] = 1
    if args.max_executor_steps is None:
        runtime_defaults["max_executor_steps"] = 2
    if args.max_actor_steps is None:
        runtime_defaults["max_actor_steps"] = 1
    if runtime_defaults:
        config = clone_system_config(config, runtime=runtime_defaults)

    workspace = _project_path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="nlrl_pipeline_smoke_"))
    assert workspace is not None
    ensure_dir(workspace)

    report = _build_data_report(config, config_path)
    _print_data_report(report)
    if not report["ok"]:
        summary = {"workspace": str(workspace), "data_report": report}
        write_json(workspace / "smoke_summary.json", summary)
        raise SystemExit(1)

    manifest = build_task_set_manifest(load_converted_dataset(config.converted_dataset_path), seed=args.seed, quota_scale=1)
    sample_output_dir = workspace / "sample"
    export_task_set_manifest(sample_output_dir, manifest)
    smoke_task_id = args.task_id or (manifest["smoke_task_ids"][0] if manifest["smoke_task_ids"] else "1")

    classic_config = clone_system_config(
        config,
        paths={
            "run_root": str(workspace / "classic_runs"),
            "skill_library_root": str(workspace / "classic_skill_library"),
            "experience_buffer_path": str(workspace / "classic_runtime" / "experience_buffer.jsonl"),
        },
    )
    reset_skill_library(classic_config.skill_library_root)
    reset_experience_buffer(classic_config.experience_buffer_path)
    classic_run_dir = SkillRLTrainer(classic_config).train_tasks(
        task_ids=[smoke_task_id],
        run_name="smoke_classic",
    )

    local_config = clone_system_config(
        config,
        paths={
            "run_root": str(workspace / "local_runs"),
            "skill_library_root": str(workspace / "local_placeholder_skill_library"),
            "experience_buffer_path": str(workspace / "local_runtime" / "experience_buffer.jsonl"),
        },
    )
    local_run_dir = TaskLocalParallelTrainer(local_config).train_tasks(
        task_ids=[smoke_task_id],
        concurrency=1,
        run_name="smoke_local",
    )

    aggregated_root = AggregatedSkillLibraryBuilder(local_config).build_from_run(
        local_run_dir,
        output_root=workspace / "aggregated_skill_library",
    )

    eval_config = clone_system_config(
        local_config,
        paths={
            "skill_library_root": str(aggregated_root),
            "run_root": str(workspace / "eval_runs"),
        },
    )
    eval_run_dir = SkillPolicyEvaluator(eval_config).evaluate_tasks(
        task_ids=[smoke_task_id],
        run_name="smoke_eval",
        concurrency=1,
    )

    summary = {
        "workspace": str(workspace),
        "config": str(config_path),
        "smoke_task_id": smoke_task_id,
        "data_report": report,
        "sample_output_dir": str(sample_output_dir),
        "classic_run_dir": str(classic_run_dir),
        "local_run_dir": str(local_run_dir),
        "aggregated_skill_library_root": str(aggregated_root),
        "evaluation_run_dir": str(eval_run_dir),
    }
    write_json(workspace / "smoke_summary.json", summary)
    print(f"workspace: {workspace}")
    print(f"smoke_task_id: {smoke_task_id}")
    print(f"classic_run_dir: {classic_run_dir}")
    print(f"local_run_dir: {local_run_dir}")
    print(f"aggregated_skill_library_root: {aggregated_root}")
    print(f"evaluation_run_dir: {eval_run_dir}")
    print(f"summary: {workspace / 'smoke_summary.json'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified runner for NL-RL data checks, training, aggregation, and evaluation.")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--config", default="system", help="Config path or alias under configs/. Default: system")
    shared.add_argument("--skill-library-root", help="Override the active skill library root.")
    shared.add_argument("--experience-buffer-path", help="Override the NL-Experience Buffer path.")
    shared.add_argument("--run-root", help="Override the output run root.")
    shared.add_argument("--dataset-path", help="Override benchmark/question.json.")
    shared.add_argument("--converted-dataset-path", help="Override the converted dataset question.json.")
    shared.add_argument("--docs-root", help="Override docs/ root.")
    shared.add_argument("--max-iterations", type=int, help="Override runtime.max_iterations_per_task.")
    shared.add_argument("--max-executor-steps", type=int, help="Override runtime.max_executor_steps.")
    shared.add_argument("--max-actor-steps", type=int, help="Override runtime.max_actor_steps.")
    shared.add_argument("--max-context-chars", type=int, help="Override runtime.max_context_chars.")
    shared.add_argument("--skill-count-limit", type=int, help="Override runtime.skill_count_limit.")
    shared.add_argument("--skill-match-threshold", type=float, help="Override runtime.skill_match_threshold.")

    task_select = argparse.ArgumentParser(add_help=False)
    task_select.add_argument("--task-ids", nargs="+", help="Normalized task ids or original question ids.")
    task_select.add_argument("--task-file", help="Newline-delimited task ids or original question ids.")
    task_select.add_argument("--count", type=int, help="Take the first N tasks after start-index.")
    task_select.add_argument("--start-index", type=int, default=0, help="Zero-based dataset offset.")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-configs", help="List config aliases available under configs/.")

    check = sub.add_parser("check-data", parents=[shared], help="Validate benchmark and converted dataset paths.")
    check.add_argument("--output", help="Optional JSON report path.")
    check.set_defaults(func=cmd_check_data)

    sample = sub.add_parser("sample", parents=[shared], help="Export the default stratified task set files.")
    sample.add_argument("--seed", type=int, default=20260403, help="Sampling seed.")
    sample.add_argument("--quota-scale", type=int, default=1, help="Multiply the default bucket quotas.")
    sample.add_argument("--output-dir", help="Optional export directory.")
    sample.set_defaults(func=cmd_sample)

    debug = sub.add_parser("train-debug", parents=[shared], help="Run the full RL loop on one task.")
    debug.add_argument("--task-id", help="Normalized task id or original question id.")
    debug.add_argument("--run-name", help="Optional run folder name.")
    debug.add_argument("--reset-skill-library", action="store_true", help="Clear the active skill library before running.")
    debug.add_argument("--reset-experience-buffer", action="store_true", help="Clear the active experience buffer before running.")
    debug.set_defaults(func=cmd_train_debug)

    classic = sub.add_parser("train-classic", parents=[shared, task_select], help="Continuous multi-task training against one shared skill library.")
    classic.add_argument("--run-name", help="Optional run folder name.")
    classic.add_argument("--reset-skill-library", action="store_true", help="Clear the active skill library before running.")
    classic.add_argument("--reset-experience-buffer", action="store_true", help="Clear the active experience buffer before running.")
    classic.set_defaults(func=cmd_train_classic)

    local = sub.add_parser("train-local", parents=[shared, task_select], help="Task-local parallel training with isolated single-skill workspaces.")
    local.add_argument("--concurrency", type=int, default=2, help="Number of task-local workers to run concurrently.")
    local.add_argument("--run-name", help="Optional run folder name.")
    local.set_defaults(func=cmd_train_local)

    aggregate = sub.add_parser("aggregate", parents=[shared], help="Aggregate a task-local run into the final 6-skill library.")
    aggregate.add_argument("--input-run-dir", required=True, help="Task-local training run directory.")
    aggregate.add_argument("--output-root", help="Optional destination for the aggregated skill library.")
    aggregate.set_defaults(func=cmd_aggregate)

    evaluate = sub.add_parser("evaluate", parents=[shared, task_select], help="Evaluate the current skill library without additional training.")
    evaluate.add_argument("--run-name", help="Optional run folder name.")
    evaluate.add_argument("--concurrency", type=int, default=1, help="Number of evaluation workers to run concurrently.")
    evaluate.set_defaults(func=cmd_evaluate)

    full = sub.add_parser("full-local", parents=[shared, task_select], help="One-click local train -> aggregate -> evaluate.")
    full.add_argument("--concurrency", type=int, default=2, help="Training concurrency for task-local workers.")
    full.add_argument("--eval-concurrency", type=int, help="Optional evaluation concurrency. Defaults to training concurrency.")
    full.add_argument("--run-name", help="Optional prefix for derived train/eval run names.")
    full.add_argument("--train-run-name", help="Optional explicit train run name.")
    full.add_argument("--eval-run-name", help="Optional explicit evaluation run name.")
    full.add_argument("--output-root", help="Optional destination for the aggregated skill library.")
    full.set_defaults(func=cmd_full_local)

    smoke = sub.add_parser("smoke", parents=[shared], help="Minimal end-to-end smoke over check-data, sample, classic train, local train, aggregate, and evaluate.")
    smoke.add_argument("--workspace", help="Optional workspace for smoke artifacts. Defaults to a temp directory.")
    smoke.add_argument("--task-id", help="Optional fixed task id for the smoke run.")
    smoke.add_argument("--seed", type=int, default=20260403, help="Sampling seed used to create the smoke task-set files.")
    smoke.set_defaults(func=cmd_smoke)

    parser.set_defaults(func=cmd_list_configs)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
