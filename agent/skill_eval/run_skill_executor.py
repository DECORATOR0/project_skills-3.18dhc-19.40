"""
CLI for end-to-end skill-based Earth-Bench execution.
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
from .executor import execute_one_question, save_batch_summary
from .network_errors import NetworkCallError
from .schemas import SkillExecutionRecord

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
    # Keep verbose mode focused on our pipeline stages instead of low-level HTTP
    # transport dumps. Raw LLM prompts/responses are persisted to files.
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


async def _run_one(qid: str, sem: asyncio.Semaphore, output_dir: Path, idx: int, total: int):
    async with sem:
        log.info("[%d/%d] Starting Q%s", idx, total, qid)
        try:
            record = await asyncio.to_thread(execute_one_question, qid, output_dir)
            metrics = record.metrics or {}
            log.info(
                "[%d/%d] Finished Q%s | skill=%s | plan=%s | answer=%s | TAO=%.4f | TIO=%.4f | TEM=%.4f | ACC=%.4f",
                idx,
                total,
                qid,
                record.skill_id,
                record.planning_source,
                record.final_choice_label or "?",
                metrics.get("tool_any_order", 0.0),
                metrics.get("tool_in_order", 0.0),
                metrics.get("tool_exact_match", 0.0),
                metrics.get("accuracy", 0.0),
            )
            return record
        except Exception as exc:
            log.exception("[%d/%d] Skill executor failed on Q%s", idx, total, qid)
            record = SkillExecutionRecord(
                question_id=qid,
                question_text="",
                data_dir="",
                skill_id="",
                planning_source="network-error-skip" if isinstance(exc, NetworkCallError) else "failed-before-execution",
                logs=[{"stage": "network_error" if isinstance(exc, NetworkCallError) else "fatal", "message": str(exc), "extra": {"network_error": isinstance(exc, NetworkCallError)}}],
                metrics={"network_error": True, "skipped": True} if isinstance(exc, NetworkCallError) else {},
            )
            question_dir = output_dir / f"question_{qid}"
            question_dir.mkdir(parents=True, exist_ok=True)
            (question_dir / "execution_record.json").write_text(
                json.dumps(asdict(record), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            (question_dir / "execution_log.json").write_text(
                json.dumps(record.logs, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            (question_dir / "llm_trace.json").write_text(
                json.dumps(
                    {
                        "planner": {},
                        "parameter_steps": [],
                        "answer_selector": {},
                        "fatal_error": str(exc),
                        "network_error": isinstance(exc, NetworkCallError),
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
            return record


async def _run_many(qids: list[str], concurrency: int, output_dir: Path):
    sem = asyncio.Semaphore(concurrency)
    total = len(qids)
    tasks = [
        asyncio.create_task(_run_one(qid, sem, output_dir, i + 1, total))
        for i, qid in enumerate(qids)
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run end-to-end skill benchmark executor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=str, default="agent/skill_eval/execution_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    qids = _select_qids(args)
    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, args.verbose)

    if qids is None:
        qids = [item.question_id for item in load_benchmark()]

    log.info("Prepared %d questions with concurrency=%d", len(qids), max(1, args.concurrency))
    log.info("Output directory: %s", output_dir)
    records = asyncio.run(_run_many(qids, max(1, args.concurrency), output_dir))
    summary_path = save_batch_summary(records, output_dir)
    records_path = output_dir / "records.json"
    records_path.write_text(
        json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        log.info("Batch summary: %s", summary)
    except Exception:
        log.warning("Could not read batch summary for console display.")

    print(f"Logs written to {log_path}")
    print(f"Records written to {records_path}")
    print(f"Summary written to {summary_path}")


if __name__ == "__main__":
    main()
