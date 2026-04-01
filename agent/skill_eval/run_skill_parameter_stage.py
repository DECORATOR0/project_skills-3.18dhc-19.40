"""
Stage 2 runner: parameter generation + tool execution from saved plans.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .staged_runner import load_plan_record, parameterize_one_question, save_parameter_question
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


def _all_qids_from_input(input_dir: Path) -> list[str]:
    qids = []
    for child in sorted(input_dir.glob("question_*")):
        if child.is_dir():
            qids.append(child.name.split("_", 1)[1])
    return qids


def _select_qids(args, input_dir: Path) -> list[str]:
    if args.question:
        return [str(args.question)]
    if args.start is not None:
        end = args.end or args.start
        return [str(i) for i in range(args.start, end + 1)]
    return _all_qids_from_input(input_dir)


async def _run_one(qid: str, idx: int, total: int, sem: asyncio.Semaphore, input_dir: Path, output_dir: Path, catalog):
    async with sem:
        log.info("[%d/%d] Parameter stage Q%s", idx, total, qid)
        plan_record = await asyncio.to_thread(load_plan_record, input_dir, qid)
        record, trace = await asyncio.to_thread(parameterize_one_question, plan_record, catalog=catalog)
        await asyncio.to_thread(save_parameter_question, record, trace, output_dir)
        log.info(
            "[%d/%d] Finished Q%s | skill=%s | ParamAcc=%.4f | executed=%d",
            idx,
            total,
            qid,
            record.skill_id,
            record.metrics.get("parameter_accuracy", 0.0),
            record.metrics.get("executed_count", 0),
        )
        return record


async def _run_many(qids: list[str], concurrency: int, input_dir: Path, output_dir: Path, catalog):
    sem = asyncio.Semaphore(concurrency)
    total = len(qids)
    tasks = [
        asyncio.create_task(_run_one(qid, i + 1, total, sem, input_dir, output_dir, catalog))
        for i, qid in enumerate(qids)
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run skill-eval stage 2 parameter execution")
    parser.add_argument("--input", type=str, required=True, help="Stage 1 plan results directory")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=str, default="agent/skill_eval/parameter_stage_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input)
    qids = _select_qids(args, input_dir)
    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, args.verbose)
    log.info("Prepared %d questions with concurrency=%d from %s", len(qids), max(1, args.concurrency), input_dir)
    catalog = build_catalog()
    records = asyncio.run(_run_many(qids, max(1, args.concurrency), input_dir, output_dir, catalog))

    records_path = output_dir / "parameter_records.json"
    records_path.write_text(json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    n = len(records) or 1
    summary = {
        "count": len(records),
        "avg_parameter_accuracy": round(sum(r.metrics.get("parameter_accuracy", 0.0) for r in records) / n, 4),
        "avg_executed_count": round(sum(r.metrics.get("executed_count", 0.0) for r in records) / n, 2),
        "avg_gold_count": round(sum(r.metrics.get("gold_count", 0.0) for r in records) / n, 2),
    }
    summary_path = output_dir / "parameter_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Stage 2 summary: %s", summary)
    print(f"Logs written to {log_path}")
    print(f"Parameter records written to {records_path}")
    print(f"Parameter summary written to {summary_path}")


if __name__ == "__main__":
    main()
