"""
Stage 3 runner: final 4-choice answer selection from saved parameter-stage results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .staged_runner import answer_one_question_async, load_parameter_record, save_answer_question

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


async def _run_one(qid: str, idx: int, total: int, sem: asyncio.Semaphore, input_dir: Path, output_dir: Path):
    async with sem:
        log.info("[%d/%d] Answer stage Q%s", idx, total, qid)
        parameter_record = await asyncio.to_thread(load_parameter_record, input_dir, qid)
        record, trace = await answer_one_question_async(parameter_record)
        await asyncio.to_thread(save_answer_question, record, trace, output_dir)
        log.info(
            "[%d/%d] Finished Q%s | answer=%s | fallback=%s | accuracy=%.4f",
            idx,
            total,
            qid,
            record.final_choice_label or "?",
            record.final_answer_fallback_used,
            record.metrics.get("accuracy", 0.0),
        )
        return record


async def _run_many(qids: list[str], concurrency: int, input_dir: Path, output_dir: Path):
    sem = asyncio.Semaphore(concurrency)
    total = len(qids)
    tasks = [
        asyncio.create_task(_run_one(qid, i + 1, total, sem, input_dir, output_dir))
        for i, qid in enumerate(qids)
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run skill-eval stage 3 final answer selection")
    parser.add_argument("--input", type=str, required=True, help="Stage 2 parameter results directory")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=str, default="agent/skill_eval/answer_stage_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input)
    qids = _select_qids(args, input_dir)
    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, args.verbose)
    log.info("Prepared %d questions with concurrency=%d from %s", len(qids), max(1, args.concurrency), input_dir)
    records = asyncio.run(_run_many(qids, max(1, args.concurrency), input_dir, output_dir))

    records_path = output_dir / "answer_records.json"
    records_path.write_text(json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    n = len(records) or 1
    summary = {
        "count": len(records),
        "avg_accuracy": round(sum(r.metrics.get("accuracy", 0.0) for r in records) / n, 4),
        "fallback_count": sum(1 for r in records if r.final_answer_fallback_used),
    }
    summary_path = output_dir / "answer_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Stage 3 summary: %s", summary)
    print(f"Logs written to {log_path}")
    print(f"Answer records written to {records_path}")
    print(f"Answer summary written to {summary_path}")


if __name__ == "__main__":
    main()
