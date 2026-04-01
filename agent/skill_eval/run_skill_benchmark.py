"""
Prepare skill-routed Earth-Bench Autonomous Planning evaluation manifests.

This script does not execute the benchmark end-to-end by itself. Instead, it
builds the exact prompts, skill assignments, tool subsets, and MCP server specs
needed by a downstream executor.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .benchmark_loader import load_benchmark
from .tool_catalog import build_catalog
from .tool_router import infer_question_profile, shortlist_tools, shortlist_to_prompt
from .mcp_servers import build_mcp_server_specs
from .prompt_builder import (
    build_choice_selector_messages,
    build_initial_context,
    build_worker_user_prompt,
)
from .skill_llm_planner import prepare_skill_planning_context
from .tool_rendering import render_detailed_tool_block, render_text_description_and_args


def _select_items(args) -> list:
    if args.question:
        qids = [str(args.question)]
    elif args.start is not None:
        end = args.end or args.start
        qids = [str(i) for i in range(args.start, end + 1)]
    else:
        qids = None
    return load_benchmark(question_ids=qids)


def _single_agent_planning_context(item, catalog) -> tuple[dict, list, str]:
    """
    Reuse the single-agent planning preparation path:
    profile inference + shortlist construction.

    This deliberately avoids the debate / reflection pipeline.
    """
    profile = infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlist_tools(
        catalog,
        item.question_text,
        item.file_list,
        question_id=item.question_id,
    )
    tools_prompt = shortlist_to_prompt(shortlisted)
    return profile, shortlisted, tools_prompt


def build_manifest_item(item, catalog, include_first_decision: bool) -> dict:
    single_profile, single_shortlist, single_tools_prompt = _single_agent_planning_context(item, catalog)
    planning_context = prepare_skill_planning_context(
        item,
        catalog=catalog,
        profile=single_profile,
        shortlisted=single_shortlist,
    )
    skill = planning_context.skill
    rendered_tools = render_text_description_and_args(planning_context.shortlisted_tools)
    initial_context = build_initial_context(item.question_text)
    initial_user_prompt = build_worker_user_prompt(
        question=item.question_text,
        data_path=item.data_dir,
        rendered_tools=rendered_tools,
        context=None,
    )
    choice_selector = build_choice_selector_messages(
        question=item.question_text,
        choices=[str(c) for c in item.choices],
        execution_summary=(
            "Fill this field with the final skill execution summary before running "
            "the answer-selector model."
        ),
    )
    payload = {
        "question_id": item.question_id,
        "question_text": item.question_text,
        "data_dir": item.data_dir,
        "choices": [str(c) for c in item.choices],
        "gold_tool_names": item.gold_tool_names,
        "planning_source": "skill-aware-single-agent-shortlist",
        "skill_id": skill.skill_id,
        "skill_display_name": skill.display_name,
        "skill_description": skill.description,
        "skill_notes": list(skill.notes),
        "skill_focus_tools": planning_context.focus_tools,
        "single_agent_profile": {
            "domain": single_profile.get("domain"),
            "intents": sorted(single_profile.get("intents", [])),
            "required_tools": sorted(single_profile.get("required_tools", [])),
            "preferred_tools": sorted(single_profile.get("preferred_tools", [])),
            "discouraged_tools": sorted(single_profile.get("discouraged_tools", [])),
            "file_priors": sorted(single_profile.get("file_priors", [])),
            "period_count_hint": single_profile.get("period_count_hint"),
            "allow_repeat_compression": single_profile.get("allow_repeat_compression"),
        },
        "single_agent_candidate_tools": [tool.canonical_name for tool in single_shortlist],
        "single_agent_tools_prompt": single_tools_prompt,
        "tool_names": [tool.canonical_name for tool in planning_context.shortlisted_tools],
        "rendered_tools_text": rendered_tools,
        "rendered_tools_detailed": render_detailed_tool_block(planning_context.shortlisted_tools),
        "planner_system_prompt": planning_context.planner_system_prompt,
        "planner_user_prompt": planning_context.planner_user_prompt,
        "initial_context": initial_context,
        "initial_worker_user_prompt": initial_user_prompt,
        "choice_selector_messages": choice_selector,
        "mcp_servers": [
            {
                "name": spec.name,
                "script_path": str(spec.script_path),
                "temp_dir": str(spec.temp_dir),
                "command": spec.command(),
            }
            for spec in build_mcp_server_specs()
        ],
    }
    if include_first_decision:
        try:
            from .parameter_worker import choose_next_tool_call

            first = choose_next_tool_call(initial_user_prompt)
            payload["first_worker_decision"] = {
                "tool_name": first.tool_name,
                "arguments": first.arguments,
                "rationale": first.rationale,
            }
        except Exception as exc:
            payload["first_worker_decision_error"] = str(exc)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare skill-based Earth-Bench manifests")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", type=str)
    group.add_argument("--start", type=int)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--end", type=int)
    parser.add_argument("--output", type=str, default="agent/skill_eval/results")
    parser.add_argument("--include-first-decision", action="store_true")
    args = parser.parse_args()

    items = _select_items(args)
    catalog = build_catalog()

    manifests = [
        build_manifest_item(item, catalog, args.include_first_decision)
        for item in items
    ]

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"skill_eval_manifest_{timestamp}.json"
    out_path.write_text(json.dumps(manifests, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Prepared {len(manifests)} Autonomous Planning manifest items")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
