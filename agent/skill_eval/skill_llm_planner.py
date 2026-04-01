"""
Skill-aware LLM planning for skill_eval.

This keeps the original single-agent planning shape:
shortlist -> question/data_dir/file list/candidate tools -> one LLM call that
returns the full tool sequence.

Skills act as soft workflow priors and prompt refinements, not hard-coded
trajectory templates. The older deterministic skill planner remains available
as a fallback only when the planner call fails or returns an unusable sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from agent.planner.prompts import SYSTEM_PLANNER, SYSTEM_SINGLE_AGENT_PLANNER, build_planner_user_message

from .config import SKILL_PLANNER_MODEL_NAME, SKILL_PLANNER_TEMPERATURE
from .network_errors import NetworkCallError
from .schemas import BenchmarkItem, PlannedSkillStep
from .skill_planner_client import async_chat_completion, chat_completion
from .skill_planner import derive_skill_plan as derive_skill_plan_fallback
from .skill_router import SkillSpec, route_skill
from .tool_catalog import ToolMeta, build_catalog
from .tool_router import infer_question_profile, shortlist_tools, shortlist_to_prompt


@dataclass
class SkillPlanningContext:
    skill: SkillSpec
    profile: dict[str, Any]
    shortlisted_tools: list[ToolMeta]
    tools_prompt: str
    planner_system_prompt: str
    planner_user_prompt: str
    focus_tools: list[str]


@dataclass
class SkillPlanningResult:
    skill: SkillSpec
    profile: dict[str, Any]
    shortlisted_tools: list[ToolMeta]
    focus_tools: list[str]
    planner_system_prompt: str
    planner_user_prompt: str
    planned_steps: list[PlannedSkillStep]
    raw_planner_output: str = ""
    planning_source: str = "skill-llm-single-shot"
    fallback_reason: str = ""


def _join_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "(none)"


def _append_valid_tool_guardrails(user_msg: str, shortlisted: list[ToolMeta]) -> str:
    valid_names = ", ".join(t.canonical_name for t in shortlisted)
    return (
        user_msg
        + "\n\n## Valid Tool Names\n"
        + "You may ONLY use these exact tool names in `tool_sequence`:\n"
        + valid_names
        + "\nDo not invent, rename, pluralize, or batch-convert tool names."
    )


def _append_single_profile_hint(user_msg: str, profile: dict[str, Any]) -> str:
    intents = ", ".join(sorted(profile.get("intents", set()))) or "general"
    required = ", ".join(sorted(profile.get("required_tools", set()))) or "(none)"
    preferred = ", ".join(sorted(profile.get("preferred_tools", set()))) or "(none)"
    discouraged = ", ".join(sorted(profile.get("discouraged_tools", set()))) or "(none)"
    file_priors = ", ".join(sorted(profile.get("file_priors", set()))) or "(none)"
    canonical_rules = profile.get("canonical_rules", [])
    rule_text = "\n".join(f"- {rule}" for rule in canonical_rules[:6]) if canonical_rules else "- Keep the trajectory benchmark-faithful and complete."
    return (
        user_msg
        + "\n\n## Benchmark Routing Prior\n"
        + f"Domain: {profile.get('domain', 'unknown')}\n"
        + f"Detected intents: {intents}\n"
        + f"Prefer these canonical tools when applicable: {required}\n"
        + f"Also prefer these nearby benchmark tools when they fit: {preferred}\n"
        + f"Avoid these tool families unless clearly necessary: {discouraged}\n"
        + f"Detected file priors: {file_priors}\n"
        + f"Period-count hint: {profile.get('period_count_hint', 1)}\n"
        + "Canonical planning rules:\n"
        + rule_text
        + "\nChoose the shortest benchmark-style sequence that satisfies the question without omitting canonical repeated blocks."
    )


def _skill_guidance_lines(item: BenchmarkItem, skill: SkillSpec, profile: dict[str, Any], focus_tools: list[str]) -> list[str]:
    q = item.question_text.lower()
    file_priors = sorted(profile.get("file_priors", []))
    period_hint = int(profile.get("period_count_hint", 1) or 1)
    lines = [
        f"Activated skill: `{skill.display_name}`.",
        "Use the skill as a strong planning prior, but still infer the final benchmark-faithful sequence from the actual question and the shortlisted tools.",
        "Do not copy a rigid template if the question wording or data evidence suggests a different shortlist-compatible sequence.",
        f"Likely core tools for this skill inside the current shortlist: {_join_or_none(focus_tools)}.",
    ]

    if period_hint >= 2:
        lines.append(
            "The question appears to compare multiple periods or windows. Keep benchmark-style repeated transform/aggregation blocks explicit before the final comparison step."
        )

    if file_priors:
        lines.append(f"File priors detected from filenames: {_join_or_none(file_priors)}.")

    if skill.skill_id == "earth-spectrum-thermal-retrieval":
        lines.extend(
            [
                "Match the thermal retrieval family to the wording: split-window, single-channel, TES, MODIS day/night, TTM, or band-ratio/PWV should each map to their corresponding canonical tool when available.",
                "When the question asks for threshold counts, ratios, or conditioned means, prefer the specialized threshold/count tool family over a generic mean chain.",
            ]
        )
    elif skill.skill_id == "earth-spectrum-drought-stress":
        lines.extend(
            [
                "Treat drought or dryness questions as indicator-first workflows: derive the dryness product, then aggregate, threshold, or compare it.",
                "For annual or trend questions, preserve year-wise aggregation structure instead of collapsing everything into one summary.",
            ]
        )
    elif skill.skill_id == "earth-product-timeseries":
        lines.extend(
            [
                "Use this skill for general product time-series reasoning rather than repeated per-period index derivation or arithmetic-heavy product composition.",
                "If filenames already indicate an existing derived product series, prefer aggregating those products directly instead of recomputing the index.",
            ]
        )
    elif skill.skill_id == "earth-product-derived-index-change":
        lines.extend(
            [
                "Preserve repeated per-period index derivation blocks when the question compares NDVI/NDWI/NDTI/NBR or cloud-masked products across dates or periods.",
                "If cloud masking is required, keep the preprocessing stage before the downstream index tool instead of skipping directly to the derived product.",
            ]
        )
    elif skill.skill_id == "earth-product-raster-arithmetic":
        lines.extend(
            [
                "Preserve arithmetic composition order. Compute required product summaries first, then apply subtract/division/percentage-change tails in the canonical order implied by the question.",
                "Avoid replacing an arithmetic workflow with a generic mean-only chain when the benchmark family depends on explicit product arithmetic.",
            ]
        )
    elif skill.skill_id == "earth-rgb-perception-change":
        lines.extend(
            [
                "First identify the RGB subfamily: scene classification, counting, grounding/centroid, pairwise geometry, segmentation-based area, or before/after change.",
                "Prefer the canonical tool family for the detected subtask rather than mixing multiple perception pipelines unnecessarily.",
            ]
        )

    if "existing_product_series" in profile.get("file_priors", set()):
        lines.append("The filenames suggest an existing product series. Only re-derive a product if the question truly requires it.")
    if "rgb_building_change" in profile.get("intents", set()):
        lines.append("For building change questions, prefer change-segmentation style workflows over detection-then-difference shortcuts.")
    if "rgb_grounding_centroid" in profile.get("intents", set()):
        lines.append("For centroid or described-region questions, prefer grounding -> centroid workflows over generic detection heuristics.")
    if "rgb_counting" in profile.get("intents", set()) and "SM3Det" in focus_tools and "InstructSAM" in focus_tools:
        lines.append("For plain object counting, prefer counting-oriented tools unless the question explicitly requires geometry or pair selection.")
    if any(word in q for word in ("difference", "compare", "between", "vs", "versus")):
        lines.append("Because this is a comparison-style question, make sure the plan reaches an explicit comparison tail such as `difference` or `percentage_change` when warranted by the shortlisted tools.")
    if "trend" in q or "annual" in q or "yearly" in q:
        lines.append("When the question asks for annual or temporal trend, keep one per-year block for each requested year before the final trend tool whenever the benchmark pattern is year-wise.")
    if skill.skill_id == "earth-spectrum-drought-stress" and ("trend" in q or "annual" in q):
        lines.append("A common drought-trend benchmark pattern is `get_filelist -> repeated compute_tvdi -> repeated annual aggregation -> compute_linear_trend`, not a single global aggregation.")
    if skill.skill_id == "earth-spectrum-thermal-retrieval" and ("trend" in q or "annual" in q):
        lines.append("For thermal yearly trends, a common benchmark pattern is `get_filelist -> repeated yearly transform/aggregate blocks -> compute_linear_trend` or `mann_kendall_test`.")
    if skill.skill_id == "earth-spectrum-thermal-retrieval" and ("threshold ratio" in q or "multi-band" in q):
        lines.append("Distinguish `calculate_threshold_ratio` from `calculate_multi_band_threshold_ratio`; prefer the exact specialized threshold tool when the question references multi-band conditions.")
    if skill.skill_id == "earth-product-timeseries" and "existing_product_series" in profile.get("file_priors", set()):
        lines.append("Because filenames already expose derived products, prefer direct aggregation tails such as `calc_batch_image_mean -> mean -> difference` over unnecessary `calculate_tif_average` transforms.")
    if skill.skill_id == "earth-product-derived-index-change":
        lines.append("Differentiate NDTI-style repeated index derivation from NTU turbidity retrieval; use `calculate_ndti` when the benchmark is phrased as NDTI or threshold-analysis over derived turbidity index.")
        lines.append("For cloud-masked water workflows, preserve explicit `apply_cloud_mask` steps before `calculate_ndwi` if masking is mentioned.")
    if skill.skill_id == "earth-product-raster-arithmetic":
        lines.append("Differentiate average-first arithmetic from sum-first arithmetic. If the benchmark asks for multi-product ratios or energy-saving indices, preserve the explicit `calc_batch_image_sum/division` chain.")
    if skill.skill_id == "earth-rgb-perception-change" and ("rank" in q or "sort" in q):
        lines.append("For ranking questions over multiple RGB images, keep repeated per-image blocks explicit rather than collapsing them into one generic summary.")

    return lines


def _skill_system_prompt(skill: SkillSpec, *, json_mode: bool) -> str:
    base = SYSTEM_PLANNER.strip() if json_mode else SYSTEM_SINGLE_AGENT_PLANNER.strip()
    return (
        base
        + "\n\n"
        + "Additional skill-aware planner rule:\n"
        + f"- You are currently planning under the `{skill.display_name}` skill.\n"
        + "- Treat the skill guidance as a strong prior for benchmark fidelity, but do not hard-code a canned trajectory.\n"
        + "- Produce the best full tool sequence for the current question from the provided shortlist.\n"
        + ("- Return valid JSON following the required schema.\n" if json_mode else "")
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Planner returned empty JSON content")
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def prepare_skill_planning_context(
    item: BenchmarkItem,
    *,
    catalog: list[ToolMeta] | None = None,
    skill: SkillSpec | None = None,
    profile: dict[str, Any] | None = None,
    shortlisted: list[ToolMeta] | None = None,
) -> SkillPlanningContext:
    catalog = catalog or build_catalog()
    skill = skill or route_skill(item.question_id, item.question_text, item.file_list)
    profile = profile or infer_question_profile(item.question_id, item.question_text, item.file_list)
    shortlisted = shortlisted or shortlist_tools(
        catalog,
        item.question_text,
        item.file_list,
        question_id=item.question_id,
    )
    tools_prompt = shortlist_to_prompt(shortlisted)
    user_msg = build_planner_user_message(
        item.question_text,
        item.data_dir,
        item.file_list,
        tools_prompt,
        item.choices or None,
    )
    user_msg = _append_single_profile_hint(user_msg, profile)
    user_msg = _append_valid_tool_guardrails(user_msg, shortlisted)

    focus_tools = [tool.canonical_name for tool in shortlisted if tool.canonical_name in set(skill.tool_allowlist)]
    guidance_lines = _skill_guidance_lines(item, skill, profile, focus_tools)
    user_msg += "\n\n## Skill Guidance\n" + "\n".join(f"- {line}" for line in guidance_lines)
    user_msg += (
        "\n\n## Planning Objective\n"
        "Use the shortlisted tools to produce one full benchmark-faithful `tool_sequence`.\n"
        "The skill guidance should improve fidelity, not replace reasoning."
    )

    return SkillPlanningContext(
        skill=skill,
        profile=profile,
        shortlisted_tools=shortlisted,
        tools_prompt=tools_prompt,
        planner_system_prompt=_skill_system_prompt(skill, json_mode=True),
        planner_user_prompt=user_msg,
        focus_tools=focus_tools,
    )


def plan_with_skill_llm(
    item: BenchmarkItem,
    *,
    catalog: list[ToolMeta] | None = None,
    skill: SkillSpec | None = None,
    profile: dict[str, Any] | None = None,
    shortlisted: list[ToolMeta] | None = None,
    temperature: float = SKILL_PLANNER_TEMPERATURE,
) -> SkillPlanningResult:
    from agent.planner.workflow import (
        _parse_tagged_single_agent_response,
        _repair_single_sequence,
        _sanitize_tool_sequence,
    )

    context = prepare_skill_planning_context(
        item,
        catalog=catalog,
        skill=skill,
        profile=profile,
        shortlisted=shortlisted,
    )
    raw = ""
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": context.planner_system_prompt},
                {"role": "user", "content": context.planner_user_prompt},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            model=SKILL_PLANNER_MODEL_NAME,
        )
        if not (raw or "").strip():
            raise ValueError("Planner returned empty response content")
        parsed = _parse_json_object(raw)
        sanitized = _sanitize_tool_sequence(parsed.get("tool_sequence", []), item, context.shortlisted_tools)
        repaired = _repair_single_sequence(sanitized, item, context.shortlisted_tools, context.profile)
        planned_steps = [
            PlannedSkillStep(tool_name=step.tool_name, rationale=step.why_needed)
            for step in repaired
        ]
        if not planned_steps:
            raise ValueError("Planner returned an empty tool sequence after sanitization/repair")
        return SkillPlanningResult(
            skill=context.skill,
            profile=context.profile,
            shortlisted_tools=context.shortlisted_tools,
            focus_tools=context.focus_tools,
            planner_system_prompt=context.planner_system_prompt,
            planner_user_prompt=context.planner_user_prompt,
            planned_steps=planned_steps,
            raw_planner_output=raw,
            planning_source="skill-llm-json",
        )
    except Exception as exc:
        if isinstance(exc, NetworkCallError):
            raise
        json_error = str(exc)
        tagged_system = _skill_system_prompt(context.skill, json_mode=False)
        tagged_raw = raw
        try:
            tagged_raw = chat_completion(
                [
                    {"role": "system", "content": tagged_system},
                    {"role": "user", "content": context.planner_user_prompt},
                ],
                temperature=temperature,
                model=SKILL_PLANNER_MODEL_NAME,
            )
            if not (tagged_raw or "").strip():
                raise ValueError("Tagged planner returned empty response content")
            parsed = _parse_tagged_single_agent_response(tagged_raw)
            sanitized = _sanitize_tool_sequence(parsed.get("tool_sequence", []), item, context.shortlisted_tools)
            repaired = _repair_single_sequence(sanitized, item, context.shortlisted_tools, context.profile)
            planned_steps = [
                PlannedSkillStep(tool_name=step.tool_name, rationale=step.why_needed)
                for step in repaired
            ]
            if not planned_steps:
                raise ValueError("Tagged planner returned an empty tool sequence after sanitization/repair")
            return SkillPlanningResult(
                skill=context.skill,
                profile=context.profile,
                shortlisted_tools=context.shortlisted_tools,
                focus_tools=context.focus_tools,
                planner_system_prompt=tagged_system,
                planner_user_prompt=context.planner_user_prompt,
                planned_steps=planned_steps,
                raw_planner_output=tagged_raw,
                planning_source="skill-llm-tagged-fallback",
                fallback_reason=f"Primary JSON planner failed: {json_error}",
            )
        except Exception as tagged_exc:
            if isinstance(tagged_exc, NetworkCallError):
                raise
            fallback_steps = derive_skill_plan_fallback(item, context.skill)
            return SkillPlanningResult(
                skill=context.skill,
                profile=context.profile,
                shortlisted_tools=context.shortlisted_tools,
                focus_tools=context.focus_tools,
                planner_system_prompt=tagged_system,
                planner_user_prompt=context.planner_user_prompt,
                planned_steps=fallback_steps,
                raw_planner_output=tagged_raw or raw,
                planning_source="skill-template-fallback",
                fallback_reason=f"Primary JSON planner failed: {json_error}; tagged fallback failed: {tagged_exc}",
            )


async def plan_with_skill_llm_async(
    item: BenchmarkItem,
    *,
    catalog: list[ToolMeta] | None = None,
    skill: SkillSpec | None = None,
    profile: dict[str, Any] | None = None,
    shortlisted: list[ToolMeta] | None = None,
    temperature: float = SKILL_PLANNER_TEMPERATURE,
) -> SkillPlanningResult:
    from agent.planner.workflow import (
        _parse_tagged_single_agent_response,
        _repair_single_sequence,
        _sanitize_tool_sequence,
    )

    context = prepare_skill_planning_context(
        item,
        catalog=catalog,
        skill=skill,
        profile=profile,
        shortlisted=shortlisted,
    )
    raw = ""
    try:
        raw = await async_chat_completion(
            [
                {"role": "system", "content": context.planner_system_prompt},
                {"role": "user", "content": context.planner_user_prompt},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            model=SKILL_PLANNER_MODEL_NAME,
        )
        if not (raw or "").strip():
            raise ValueError("Planner returned empty response content")
        parsed = _parse_json_object(raw)
        sanitized = _sanitize_tool_sequence(parsed.get("tool_sequence", []), item, context.shortlisted_tools)
        repaired = _repair_single_sequence(sanitized, item, context.shortlisted_tools, context.profile)
        planned_steps = [
            PlannedSkillStep(tool_name=step.tool_name, rationale=step.why_needed)
            for step in repaired
        ]
        if not planned_steps:
            raise ValueError("Planner returned an empty tool sequence after sanitization/repair")
        return SkillPlanningResult(
            skill=context.skill,
            profile=context.profile,
            shortlisted_tools=context.shortlisted_tools,
            focus_tools=context.focus_tools,
            planner_system_prompt=context.planner_system_prompt,
            planner_user_prompt=context.planner_user_prompt,
            planned_steps=planned_steps,
            raw_planner_output=raw,
            planning_source="skill-llm-json",
        )
    except Exception as exc:
        if isinstance(exc, NetworkCallError):
            raise
        json_error = str(exc)
        tagged_system = _skill_system_prompt(context.skill, json_mode=False)
        tagged_raw = raw
        try:
            tagged_raw = await async_chat_completion(
                [
                    {"role": "system", "content": tagged_system},
                    {"role": "user", "content": context.planner_user_prompt},
                ],
                temperature=temperature,
                model=SKILL_PLANNER_MODEL_NAME,
            )
            if not (tagged_raw or "").strip():
                raise ValueError("Tagged planner returned empty response content")
            parsed = _parse_tagged_single_agent_response(tagged_raw)
            sanitized = _sanitize_tool_sequence(parsed.get("tool_sequence", []), item, context.shortlisted_tools)
            repaired = _repair_single_sequence(sanitized, item, context.shortlisted_tools, context.profile)
            planned_steps = [
                PlannedSkillStep(tool_name=step.tool_name, rationale=step.why_needed)
                for step in repaired
            ]
            if not planned_steps:
                raise ValueError("Tagged planner returned an empty tool sequence after sanitization/repair")
            return SkillPlanningResult(
                skill=context.skill,
                profile=context.profile,
                shortlisted_tools=context.shortlisted_tools,
                focus_tools=context.focus_tools,
                planner_system_prompt=tagged_system,
                planner_user_prompt=context.planner_user_prompt,
                planned_steps=planned_steps,
                raw_planner_output=tagged_raw,
                planning_source="skill-llm-tagged-fallback",
                fallback_reason=f"Primary JSON planner failed: {json_error}",
            )
        except Exception as tagged_exc:
            if isinstance(tagged_exc, NetworkCallError):
                raise
            fallback_steps = derive_skill_plan_fallback(item, context.skill)
            return SkillPlanningResult(
                skill=context.skill,
                profile=context.profile,
                shortlisted_tools=context.shortlisted_tools,
                focus_tools=context.focus_tools,
                planner_system_prompt=tagged_system,
                planner_user_prompt=context.planner_user_prompt,
                planned_steps=fallback_steps,
                raw_planner_output=tagged_raw or raw,
                planning_source="skill-template-fallback",
                fallback_reason=f"Primary JSON planner failed: {json_error}; tagged fallback failed: {tagged_exc}",
            )
