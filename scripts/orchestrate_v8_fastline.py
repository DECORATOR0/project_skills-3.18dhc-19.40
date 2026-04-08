#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nlrl_skills.config import SystemConfig, clone_system_config, load_system_config
from nlrl_skills.evaluator_runner import SkillPolicyEvaluator
from nlrl_skills.task_local_trainer import TaskLocalParallelTrainer
from nlrl_skills.skill_aggregator_v8 import V8AggregatedSkillLibraryBuilder
from nlrl_skills.utils import ensure_dir, write_json

CONFIG_ROOT = PROJECT_ROOT / "configs"
DEFAULT_API_INFO_PATH = Path("/data/xsy/skill-pool/API说明/最新api说明.txt")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw or default


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


def _read_task_file(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_qwen_api_profile(api_info_path: Path) -> dict[str, str]:
    text = api_info_path.read_text(encoding="utf-8")
    primary_base_match = re.search(r"http://35\.220\.164\.252:3888/v1", text)
    if not primary_base_match:
        generic_match = re.search(r"https?://[^\s]+/v1", text)
        if not generic_match:
            raise RuntimeError(f"Could not resolve qwen base_url from {api_info_path}")
        base_url = generic_match.group(0)
    else:
        base_url = primary_base_match.group(0)

    api_key = ""
    seen_shlab = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "上海实验室api" in line:
            seen_shlab = True
            continue
        if seen_shlab:
            match = re.search(r"(sk-[A-Za-z0-9-]+)", line)
            if match and "sssaicode" not in match.group(1).lower():
                api_key = match.group(1)
                break
    if not api_key:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            match = re.search(r"(sk-[A-Za-z0-9-]+)", line)
            if not match:
                continue
            candidate = match.group(1)
            if "sssaicode" in candidate.lower():
                continue
            if candidate.startswith("sk-fJ"):
                continue
            api_key = candidate
            break
    if not api_key:
        raise RuntimeError(f"Could not resolve qwen api_key from {api_info_path}")
    return {
        "model": "qwen3-8b",
        "base_url": base_url,
        "api_key": api_key,
    }


def _build_v8_config(
    base_config: SystemConfig,
    *,
    qwen_profile: dict[str, str],
    run_root: Path,
    skill_library_root: Path,
    experience_buffer_path: Path,
) -> SystemConfig:
    executor_max_tokens = _env_int("NLRL_EXECUTOR_MAX_TOKENS", 8192)
    executor_total_token_budget = _env_int("NLRL_RUNTIME_EXECUTOR_TOTAL_TOKEN_BUDGET", 32768)
    executor_tokenizer_path = _env_str("NLRL_RUNTIME_EXECUTOR_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B")
    return clone_system_config(
        base_config,
        router={
            "model": qwen_profile["model"],
            "base_url": qwen_profile["base_url"],
            "api_key": qwen_profile["api_key"],
            "timeout_seconds": 300,
            "enable_thinking": True,
            "stream": True,
            "max_tokens": 2048,
        },
        executor={
            "model": qwen_profile["model"],
            "base_url": qwen_profile["base_url"],
            "api_key": qwen_profile["api_key"],
            "timeout_seconds": 300,
            "enable_thinking": True,
            "stream": True,
            "max_tokens": executor_max_tokens,
        },
        paths={
            "run_root": run_root,
            "skill_library_root": skill_library_root,
            "experience_buffer_path": experience_buffer_path,
        },
        runtime={
            "executor_total_token_budget": executor_total_token_budget,
            "executor_tokenizer_path": executor_tokenizer_path,
        },
    )


def _empty_usage() -> dict[str, Any]:
    return {
        "request_count": 0,
        "calls_with_usage": 0,
        "calls_without_usage": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "sum_llm_elapsed_seconds": 0.0,
        "by_model": {},
    }


def _update_usage_bucket(bucket: dict[str, Any], *, model: str, usage: dict | None, elapsed_seconds: float | None) -> None:
    bucket["request_count"] += 1
    if elapsed_seconds is not None:
        try:
            bucket["sum_llm_elapsed_seconds"] += float(elapsed_seconds)
        except Exception:
            pass
    model_bucket = bucket["by_model"].setdefault(
        model,
        {
            "request_count": 0,
            "calls_with_usage": 0,
            "calls_without_usage": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "sum_llm_elapsed_seconds": 0.0,
        },
    )
    model_bucket["request_count"] += 1
    if elapsed_seconds is not None:
        try:
            model_bucket["sum_llm_elapsed_seconds"] += float(elapsed_seconds)
        except Exception:
            pass
    if not isinstance(usage, dict):
        bucket["calls_without_usage"] += 1
        model_bucket["calls_without_usage"] += 1
        return
    bucket["calls_with_usage"] += 1
    model_bucket["calls_with_usage"] += 1
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            bucket[key] += value
            model_bucket[key] += value


def collect_llm_usage(root: Path) -> dict[str, Any]:
    stats = _empty_usage()
    if not root.exists():
        return stats
    for response_path in sorted(root.rglob("*_response.json")):
        try:
            data = json.loads(response_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        model = str(data.get("request_model", "")).strip() or "unknown"
        usage = data.get("usage")
        elapsed_seconds = data.get("elapsed_seconds")
        _update_usage_bucket(stats, model=model, usage=usage, elapsed_seconds=elapsed_seconds)
    return stats


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _section_report_md(section_name: str, section_summary: dict[str, Any]) -> str:
    train_metrics = section_summary["train"]["run_summary"]["training_metrics"]
    aggregation = section_summary["aggregate"]["aggregation_summary"]
    flat_eval = section_summary["evaluate_flat"]["evaluation_summary"]
    tree_eval = section_summary["evaluate_tree"]["evaluation_summary"]
    train_wall_seconds = section_summary["train"].get("wall_seconds")
    train_wall_label = f"{train_wall_seconds:.2f}" if isinstance(train_wall_seconds, (int, float)) else "n/a"
    lines = [
        f"## {section_name}",
        "",
        f"- task_count: `{section_summary['task_count']}`",
        f"- train wall_seconds: `{train_wall_label}`",
        f"- aggregate wall_seconds: `{section_summary['aggregate']['wall_seconds']:.2f}`",
        f"- flat eval wall_seconds: `{section_summary['evaluate_flat']['wall_seconds']:.2f}`",
        f"- tree eval wall_seconds: `{section_summary['evaluate_tree']['wall_seconds']:.2f}`",
        f"- train retained_count: `{section_summary['train']['run_summary']['retained_count']}`",
        f"- train success_count: `{section_summary['train']['run_summary']['success_count']}`",
        f"- train accuracy: `{train_metrics['avg_metrics']['accuracy']}`",
        f"- actual cluster_count: `{aggregation['actual_cluster_count']}`",
        f"- flat success_count: `{flat_eval['success_count']}` / `{flat_eval['task_count']}`",
        f"- flat accuracy: `{flat_eval['avg_metrics']['accuracy']}`",
        f"- tree success_count: `{tree_eval['success_count']}` / `{tree_eval['task_count']}`",
        f"- tree accuracy: `{tree_eval['avg_metrics']['accuracy']}`",
        "",
        "### Token Summary",
        "",
        f"- train total_tokens: `{section_summary['train']['llm_usage']['total_tokens']}`",
        f"- aggregate total_tokens: `{section_summary['aggregate']['llm_usage']['total_tokens']}`",
        f"- flat eval total_tokens: `{section_summary['evaluate_flat']['llm_usage']['total_tokens']}`",
        f"- tree eval total_tokens: `{section_summary['evaluate_tree']['llm_usage']['total_tokens']}`",
        "",
    ]
    return "\n".join(lines)


def run_section(
    *,
    section_name: str,
    task_file: Path,
    base_config: SystemConfig,
    qwen_profile: dict[str, str],
    pipeline_dir: Path,
    cluster_count: int,
    train_concurrency: int,
    aggregation_concurrency: int,
    eval_concurrency: int,
) -> dict[str, Any]:
    task_ids = _read_task_file(task_file)
    section_dir = ensure_dir(pipeline_dir / section_name)
    copied_task_file = section_dir / task_file.name
    copied_task_file.write_text("\n".join(task_ids) + "\n", encoding="utf-8")

    train_config = _build_v8_config(
        base_config,
        qwen_profile=qwen_profile,
        run_root=ensure_dir(section_dir / "train_runs"),
        skill_library_root=ensure_dir(section_dir / "placeholder_skill_library"),
        experience_buffer_path=section_dir / "runtime" / "experience_buffer.jsonl",
    )
    train_started_at = datetime.now().isoformat(timespec="seconds")
    train_started = time.perf_counter()
    train_run_dir = TaskLocalParallelTrainer(train_config).train_tasks(
        task_ids=task_ids,
        concurrency=train_concurrency,
        run_name=f"{section_name}_train",
    )
    train_wall_seconds = time.perf_counter() - train_started
    train_run_summary = _load_json(train_run_dir / "run_summary.json")

    aggregate_started = time.perf_counter()
    aggregated_root = V8AggregatedSkillLibraryBuilder(train_config).build_from_run(
        train_run_dir,
        output_root=section_dir / "aggregated_skill_library_v8",
        cluster_count=cluster_count,
        aggregation_concurrency=aggregation_concurrency,
    )
    aggregate_wall_seconds = time.perf_counter() - aggregate_started
    aggregation_summary = _load_json(aggregated_root / "aggregation_summary.json")

    flat_config = _build_v8_config(
        base_config,
        qwen_profile=qwen_profile,
        run_root=ensure_dir(section_dir / "eval_runs_flat"),
        skill_library_root=aggregated_root / "flat",
        experience_buffer_path=section_dir / "eval_runtime_flat" / "experience_buffer.jsonl",
    )
    flat_started = time.perf_counter()
    flat_eval_run_dir = SkillPolicyEvaluator(flat_config).evaluate_tasks(
        task_ids=task_ids,
        run_name=f"{section_name}_flat_eval",
        concurrency=eval_concurrency,
    )
    flat_wall_seconds = time.perf_counter() - flat_started
    flat_eval_summary = _load_json(flat_eval_run_dir / "evaluation_summary.json")

    tree_config = _build_v8_config(
        base_config,
        qwen_profile=qwen_profile,
        run_root=ensure_dir(section_dir / "eval_runs_tree"),
        skill_library_root=aggregated_root / "tree",
        experience_buffer_path=section_dir / "eval_runtime_tree" / "experience_buffer.jsonl",
    )
    tree_started = time.perf_counter()
    tree_eval_run_dir = SkillPolicyEvaluator(tree_config).evaluate_tasks(
        task_ids=task_ids,
        run_name=f"{section_name}_tree_eval",
        concurrency=eval_concurrency,
    )
    tree_wall_seconds = time.perf_counter() - tree_started
    tree_eval_summary = _load_json(tree_eval_run_dir / "evaluation_summary.json")

    section_summary = {
        "section_name": section_name,
        "task_file": str(task_file),
        "task_count": len(task_ids),
        "train": {
            "started_at": train_started_at,
            "run_dir": str(train_run_dir),
            "run_summary": train_run_summary,
            "wall_seconds": train_wall_seconds,
            "llm_usage": collect_llm_usage(train_run_dir),
        },
        "aggregate": {
            "output_root": str(aggregated_root),
            "aggregation_summary": aggregation_summary,
            "wall_seconds": aggregate_wall_seconds,
            "llm_usage": collect_llm_usage(aggregated_root / "_aggregation_logs"),
        },
        "evaluate_flat": {
            "run_dir": str(flat_eval_run_dir),
            "evaluation_summary": flat_eval_summary,
            "wall_seconds": flat_wall_seconds,
            "llm_usage": collect_llm_usage(flat_eval_run_dir),
        },
        "evaluate_tree": {
            "run_dir": str(tree_eval_run_dir),
            "evaluation_summary": tree_eval_summary,
            "wall_seconds": tree_wall_seconds,
            "llm_usage": collect_llm_usage(tree_eval_run_dir),
        },
    }
    write_json(section_dir / "section_summary.json", section_summary)
    return section_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the v8 fastline smoke + formal 60 pipeline.")
    parser.add_argument(
        "--config",
        default="system.train_local_actor_critic_sssai_gpt52_local_qwen3_8b_gpu1",
        help="Base config path or alias under configs/.",
    )
    parser.add_argument(
        "--smoke-task-file",
        default=str(PROJECT_ROOT / "data/task_sets/parallel_skill_training_seed_20260403_formal60/smoke_3.txt"),
        help="Smoke task-id file.",
    )
    parser.add_argument(
        "--formal-task-file",
        default=str(PROJECT_ROOT / "data/task_sets/parallel_skill_training_seed_20260403_formal60/train_all_60.txt"),
        help="Formal task-id file.",
    )
    parser.add_argument(
        "--pipeline-root",
        default=str(PROJECT_ROOT / "runs"),
        help="Parent directory where the pipeline workspace will be created.",
    )
    parser.add_argument("--run-name", default=f"v8_fastline_{datetime.now().strftime('%Y%m%d_%H%M%S')}", help="Pipeline workspace name.")
    parser.add_argument("--cluster-count", type=int, default=6, help="Requested cluster count for formal aggregation.")
    parser.add_argument("--train-concurrency", type=int, default=30, help="Task-local training concurrency.")
    parser.add_argument("--aggregation-concurrency", type=int, default=30, help="Aggregation worker concurrency.")
    parser.add_argument("--eval-concurrency", type=int, default=60, help="Evaluation concurrency.")
    parser.add_argument("--api-info-path", default=str(DEFAULT_API_INFO_PATH), help="Path to the qwen API notes file.")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip the smoke section.")
    parser.add_argument("--skip-formal", action="store_true", help="Skip the formal 60 section.")
    return parser


def main() -> None:
    os.environ["NLRL_LLM_STREAM_INCLUDE_USAGE"] = "1"
    os.environ.setdefault("NLRL_LLM_MAX_CONCURRENT_REQUESTS", "60")
    os.environ.setdefault("NLRL_ACTOR_MAX_CONCURRENT_REQUESTS", "1")
    os.environ.setdefault("NLRL_CRITIC_MAX_CONCURRENT_REQUESTS", "1")
    os.environ.setdefault("NLRL_AGGREGATOR_MAX_CONCURRENT_REQUESTS", "30")
    os.environ.setdefault("NLRL_ROUTER_MAX_CONCURRENT_REQUESTS", "60")
    os.environ.setdefault("NLRL_EXECUTOR_MAX_CONCURRENT_REQUESTS", "60")
    parser = build_parser()
    parsed = parser.parse_args()

    config_path = _resolve_config_path(parsed.config)
    base_config = load_system_config(config_path)
    qwen_profile = _load_qwen_api_profile(Path(parsed.api_info_path))

    pipeline_root = ensure_dir(Path(parsed.pipeline_root))
    pipeline_dir = ensure_dir(pipeline_root / parsed.run_name)
    write_json(
        pipeline_dir / "pipeline_context.json",
        {
            "config_path": str(config_path),
            "api_info_path": str(parsed.api_info_path),
            "qwen_base_url": qwen_profile["base_url"],
            "qwen_model": qwen_profile["model"],
            "executor_total_token_budget": 32768,
            "executor_tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3-8B",
            "train_concurrency": parsed.train_concurrency,
            "aggregation_concurrency": parsed.aggregation_concurrency,
            "eval_concurrency": parsed.eval_concurrency,
            "cluster_count": parsed.cluster_count,
        },
    )

    sections: list[dict[str, Any]] = []
    if not parsed.skip_smoke:
        sections.append(
            run_section(
                section_name="smoke",
                task_file=Path(parsed.smoke_task_file),
                base_config=base_config,
                qwen_profile=qwen_profile,
                pipeline_dir=pipeline_dir,
                cluster_count=parsed.cluster_count,
                train_concurrency=parsed.train_concurrency,
                aggregation_concurrency=parsed.aggregation_concurrency,
                eval_concurrency=parsed.eval_concurrency,
            )
        )
    if not parsed.skip_formal:
        sections.append(
            run_section(
                section_name="formal60",
                task_file=Path(parsed.formal_task_file),
                base_config=base_config,
                qwen_profile=qwen_profile,
                pipeline_dir=pipeline_dir,
                cluster_count=parsed.cluster_count,
                train_concurrency=parsed.train_concurrency,
                aggregation_concurrency=parsed.aggregation_concurrency,
                eval_concurrency=parsed.eval_concurrency,
            )
        )

    pipeline_summary = {
        "pipeline_dir": str(pipeline_dir),
        "sections": sections,
    }
    write_json(pipeline_dir / "pipeline_summary.json", pipeline_summary)

    report_lines = ["# v8 Fastline Pipeline Report", ""]
    for section in sections:
        report_lines.append(_section_report_md(section["section_name"], section))
    report_path = pipeline_dir / "pipeline_report.md"
    report_path.write_text("\n".join(report_lines).strip() + "\n", encoding="utf-8")

    print(f"pipeline_dir: {pipeline_dir}")
    print(f"summary: {pipeline_dir / 'pipeline_summary.json'}")
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
