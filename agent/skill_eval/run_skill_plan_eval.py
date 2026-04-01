"""
Evaluate only the planning capability of the skill system.

This does not execute tools, does not generate parameters, and does not run the
final 4-choice answer selector. It evaluates only the planned tool sequence
against the gold Earth-Bench trajectory using TAO/TIO/TEM.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .benchmark_loader import load_benchmark
from .evaluator import tool_any_order, tool_exact_match, tool_in_order
from .network_errors import NetworkCallError
from .skill_llm_planner import plan_with_skill_llm
from .skill_router import route_skill
from .tool_catalog import build_catalog
from .tool_router import infer_question_profile, shortlist_tools

log = logging.getLogger(__name__)


@dataclass
class SkillPlanEvalRecord:
    question_id: str
    skill_id: str
    question_text: str
    planned_tools: list[str]
    gold_tools: list[str]
    metrics: dict
    planning_source: str = ""
    planner_system_prompt: str = ""
    planner_user_prompt: str = ""
    planner_raw_output: str = ""
    fallback_reason: str = ""


def _select_items(args):
    if args.question:
        qids = [str(args.question)]
    elif args.start is not None:
        end = args.end or args.start
        qids = [str(i) for i in range(args.start, end + 1)]
    else:
        qids = None
    return load_benchmark(question_ids=qids)


def evaluate_item(item, catalog) -> SkillPlanEvalRecord:
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(catalog, item.question_text, item.file_list, question_id=item.question_id)
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    try:
        plan_result = plan_with_skill_llm(
            item,
            catalog=catalog,
            skill=skill,
            profile=profile,
            shortlisted=shortlisted,
        )
    except NetworkCallError as exc:
        return SkillPlanEvalRecord(
            question_id=item.question_id,
            skill_id=skill.skill_id,
            question_text=item.question_text,
            planned_tools=[],
            gold_tools=item.gold_tool_names,
            metrics={
                "tool_any_order": 0.0,
                "tool_in_order": 0.0,
                "tool_exact_match": 0.0,
                "predicted_count": 0,
                "gold_count": len(item.gold_tool_names),
                "network_error": True,
                "skipped": True,
            },
            planning_source="network-error-skip",
            fallback_reason=str(exc),
        )
    planned_tools = [step.tool_name for step in plan_result.planned_steps]
    gold_tools = item.gold_tool_names
    metrics = {
        "tool_any_order": round(tool_any_order(planned_tools, gold_tools), 4),
        "tool_in_order": round(tool_in_order(planned_tools, gold_tools), 4),
        "tool_exact_match": round(tool_exact_match(planned_tools, gold_tools), 4),
        "predicted_count": len(planned_tools),
        "gold_count": len(gold_tools),
    }
    return SkillPlanEvalRecord(
        question_id=item.question_id,
        skill_id=skill.skill_id,
        question_text=item.question_text,
        planned_tools=planned_tools,
        gold_tools=gold_tools,
        metrics=metrics,
        planning_source=plan_result.planning_source,
        planner_system_prompt=plan_result.planner_system_prompt,
        planner_user_prompt=plan_result.planner_user_prompt,
        planner_raw_output=plan_result.raw_planner_output,
        fallback_reason=plan_result.fallback_reason,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate only skill planning TAO/TIO/TEM")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--output", type=str, default="agent/skill_eval/plan_eval_results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for noisy_name in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy_name).setLevel(logging.INFO if args.verbose else logging.WARNING)

    items = _select_items(args)
    catalog = build_catalog()
    log.info("Running plan-only eval for %d questions", len(items))
    records = []
    for idx, item in enumerate(items, start=1):
        log.info("[%d/%d] Planning Q%s", idx, len(items), item.question_id)
        record = evaluate_item(item, catalog)
        log.info(
            "[%d/%d] Finished Q%s | skill=%s | source=%s | TAO=%.4f | TIO=%.4f | TEM=%.4f",
            idx,
            len(items),
            item.question_id,
            record.skill_id,
            record.planning_source,
            record.metrics["tool_any_order"],
            record.metrics["tool_in_order"],
            record.metrics["tool_exact_match"],
        )
        records.append(record)
    out_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    details_path = out_dir / "plan_eval_records.json"
    details_path.write_text(json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False), encoding="utf-8")

    n = len(records) or 1
    summary = {
        "count": len(records),
        "avg_tool_any_order": round(sum(r.metrics["tool_any_order"] for r in records) / n, 4),
        "avg_tool_in_order": round(sum(r.metrics["tool_in_order"] for r in records) / n, 4),
        "avg_tool_exact_match": round(sum(r.metrics["tool_exact_match"] for r in records) / n, 4),
        "avg_predicted_count": round(sum(r.metrics["predicted_count"] for r in records) / n, 2),
        "avg_gold_count": round(sum(r.metrics["gold_count"] for r in records) / n, 2),
    }
    summary_path = out_dir / "plan_eval_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved plan-only records to {details_path}")
    print(f"Saved plan-only summary to {summary_path}")


if __name__ == "__main__":
    main()
