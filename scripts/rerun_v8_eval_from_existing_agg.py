#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nlrl_skills.config import load_system_config
from nlrl_skills.evaluator_runner import SkillPolicyEvaluator
from nlrl_skills.utils import ensure_dir, write_json
from scripts.orchestrate_v8_fastline import (
    _build_v8_config,
    _load_qwen_api_profile,
    _resolve_config_path,
)

DEFAULT_API_INFO_PATH = Path("/data/xsy/skill-pool/API说明/最新api说明.txt")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw or default


def _read_task_ids(*, task_file: str | None, task_ids: list[str] | None) -> list[str]:
    if task_ids:
        return [task_id.strip() for task_id in task_ids if task_id.strip()]
    if task_file:
        path = Path(task_file).resolve()
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    raise SystemExit("Provide either --task-file or --task-ids.")


def _branch_run_root(pipeline_dir: Path, branch: str) -> Path:
    if branch == "flat":
        return ensure_dir(pipeline_dir / "eval_runs_flat")
    if branch == "tree":
        return ensure_dir(pipeline_dir / "eval_runs_tree_parallel")
    raise ValueError(f"Unsupported branch: {branch}")


def _branch_run_name(branch: str) -> str:
    if branch == "flat":
        return "formal60_flat_eval"
    if branch == "tree":
        return "formal60_tree_eval_parallel"
    raise ValueError(f"Unsupported branch: {branch}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rerun v8 flat/tree eval from an existing aggregated skill library.")
    parser.add_argument(
        "--config",
        default="system.train_local_actor_critic_sssai_gpt52_local_qwen3_8b_gpu1",
        help="Base config path or alias under configs/.",
    )
    parser.add_argument("--task-file", help="Newline-delimited task file.")
    parser.add_argument("--task-ids", nargs="+", help="Task ids or original question ids.")
    parser.add_argument("--aggregated-root", required=True, help="Existing aggregated_skill_library_v8 root.")
    parser.add_argument(
        "--pipeline-root",
        default=str(PROJECT_ROOT / "runs"),
        help="Parent directory where the rerun workspace will be created.",
    )
    parser.add_argument(
        "--run-name",
        default=f"v8_executor_token_budget_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Workspace name for this rerun.",
    )
    parser.add_argument(
        "--branch",
        choices=["flat", "tree", "both"],
        default="both",
        help="Which branch to run. Use separate invocations if you want flat/tree to run in parallel.",
    )
    parser.add_argument("--concurrency", type=int, default=60, help="Evaluation concurrency.")
    parser.add_argument("--api-info-path", default=str(DEFAULT_API_INFO_PATH), help="Path to the qwen API notes file.")
    return parser


def _run_branch(
    *,
    branch: str,
    base_config_path: Path,
    qwen_profile: dict[str, str],
    task_ids: list[str],
    aggregated_root: Path,
    pipeline_dir: Path,
    concurrency: int,
) -> Path:
    base_config = load_system_config(base_config_path)
    branch_config = _build_v8_config(
        base_config,
        qwen_profile=qwen_profile,
        run_root=_branch_run_root(pipeline_dir, branch),
        skill_library_root=(aggregated_root / branch).resolve(),
        experience_buffer_path=pipeline_dir / f"eval_runtime_{branch}" / "experience_buffer.jsonl",
    )
    return SkillPolicyEvaluator(branch_config).evaluate_tasks(
        task_ids=task_ids,
        run_name=_branch_run_name(branch),
        concurrency=concurrency,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    task_ids = _read_task_ids(task_file=args.task_file, task_ids=args.task_ids)
    base_config_path = _resolve_config_path(args.config)
    qwen_profile = _load_qwen_api_profile(Path(args.api_info_path))
    aggregated_root = Path(args.aggregated_root).resolve()
    pipeline_dir = ensure_dir(Path(args.pipeline_root).resolve() / args.run_name)

    os.environ["NLRL_LLM_STREAM_INCLUDE_USAGE"] = "1"
    os.environ.setdefault("NLRL_LLM_MAX_CONCURRENT_REQUESTS", str(args.concurrency))
    os.environ.setdefault("NLRL_ROUTER_MAX_CONCURRENT_REQUESTS", str(args.concurrency))
    os.environ.setdefault("NLRL_EXECUTOR_MAX_CONCURRENT_REQUESTS", str(args.concurrency))

    branches = ["flat", "tree"] if args.branch == "both" else [args.branch]
    executor_total_token_budget = _env_int("NLRL_RUNTIME_EXECUTOR_TOTAL_TOKEN_BUDGET", 32768)
    executor_max_tokens = _env_int("NLRL_EXECUTOR_MAX_TOKENS", 8192)
    executor_tokenizer_path = _env_str("NLRL_RUNTIME_EXECUTOR_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B")

    write_json(
        pipeline_dir / "pipeline_context.json",
        {
            "config_path": str(base_config_path),
            "api_info_path": str(args.api_info_path),
            "qwen_base_url": qwen_profile["base_url"],
            "qwen_model": qwen_profile["model"],
            "aggregated_root": str(aggregated_root),
            "task_count": len(task_ids),
            "task_ids": task_ids,
            "branches": branches,
            "eval_concurrency": args.concurrency,
            "executor_total_token_budget": executor_total_token_budget,
            "executor_max_tokens": executor_max_tokens,
            "executor_input_token_budget": max(executor_total_token_budget - executor_max_tokens, 0),
            "executor_tokenizer_path": executor_tokenizer_path,
        },
    )

    for branch in branches:
        run_dir = _run_branch(
            branch=branch,
            base_config_path=base_config_path,
            qwen_profile=qwen_profile,
            task_ids=task_ids,
            aggregated_root=aggregated_root,
            pipeline_dir=pipeline_dir,
            concurrency=args.concurrency,
        )
        print(f"{branch}_run_dir: {run_dir}")


if __name__ == "__main__":
    main()
