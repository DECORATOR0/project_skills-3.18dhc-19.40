"""
Stage 1 runner: skill-aware planning only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .benchmark_loader import load_benchmark
from .staged_runner import plan_one_question_async, save_plan_question
from .tool_catalog import build_catalog

log = logging.getLogger(__name__)


def setup_logging(output_dir: Path, verbose: bool = False) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    handlers = [logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    for noisy_name in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy_name).setLevel(logging.INFO if verbose else logging.WARNING)
    return log_path


def _select_qids(args) -> list[str] | None:
    if args.question:
        return [str(args.question)]
    if args.start is not None:
        end = args.end or args.start
        return [str(i) for i in range(args.start, end + 1)]
    return None


async def _run_one(qid: str, idx: int, total: int, sem: asyncio.Semaphore, output_dir: Path, catalog):
    async with sem:
        log.info("[%d/%d] Planning Q%s", idx, total, qid)
        record, trace = await plan_one_question_async(qid, catalog=catalog)
        await asyncio.to_thread(save_plan_question, record, trace, output_dir)
        log.info(
            "[%d/%d] Finished Q%s | skill=%s | source=%s | TAO=%.4f | TIO=%.4f | TEM=%.4f | Eff=%.4f",
            idx,
            total,
            qid,
            record.skill_id,
            record.planning_source,
            record.metrics.get("tool_any_order", 0.0),
            record.metrics.get("tool_in_order", 0.0),
            record.metrics.get("tool_exact_match", 0.0),
            record.metrics.get("efficiency", 0.0),
        )
        return record


async def _run_many(qids: list[str], concurrency: int, output_dir: Path, catalog):
    sem = asyncio.Semaphore(concurrency)
    total = len(qids)
    tasks = [
        asyncio.create_task(_run_one(qid, i + 1, total, sem, output_dir, catalog))
        for i, qid in enumerate(qids)
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run skill-eval stage 1 planning")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=str, default="agent/skill_eval/plan_stage_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    qids = _select_qids(args)
    if qids is None:
        qids = [item.question_id for item in load_benchmark()]

    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, args.verbose)
    log.info("Prepared %d questions with concurrency=%d", len(qids), max(1, args.concurrency))
    catalog = build_catalog()
    records = asyncio.run(_run_many(qids, max(1, args.concurrency), output_dir, catalog))

    records_path = output_dir / "plan_records.json"
    records_path.write_text(json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    n = len(records) or 1
    summary = {
        "count": len(records),
        "avg_tool_any_order": round(sum(r.metrics.get("tool_any_order", 0.0) for r in records) / n, 4),
        "avg_tool_in_order": round(sum(r.metrics.get("tool_in_order", 0.0) for r in records) / n, 4),
        "avg_tool_exact_match": round(sum(r.metrics.get("tool_exact_match", 0.0) for r in records) / n, 4),
        "avg_efficiency": round(sum(r.metrics.get("efficiency", 0.0) for r in records) / n, 4),
        "planning_sources": {
            src: sum(1 for r in records if r.planning_source == src)
            for src in sorted({r.planning_source for r in records})
        },
    }
    summary_path = output_dir / "plan_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Stage 1 summary: %s", summary)
    print(f"Logs written to {log_path}")
    print(f"Plan records written to {records_path}")
    print(f"Plan summary written to {summary_path}")


if __name__ == "__main__":
    main()
