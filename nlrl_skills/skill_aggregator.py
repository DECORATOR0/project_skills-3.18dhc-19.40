from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from agent.skill_eval.skill_router import SKILL_SPECS

import yaml

from .config import LLMConfig, SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import LLMMessage, SkillDetail
from .skills import discover_skills, load_skill_detail, write_skill_bundle
from .utils import ensure_dir, read_json, read_text, slugify, write_json

FAMILY_TO_DOMAIN = {
    "earth-spectrum-thermal-retrieval": "spectrum",
    "earth-spectrum-drought-stress": "spectrum",
    "earth-product-timeseries": "products",
    "earth-product-derived-index-change": "products",
    "earth-product-raster-arithmetic": "products",
    "earth-rgb-perception-change": "rgb",
}

GENERIC_RESOURCE_TOOLS = {"get_filelist", "read_file", "run_python_script"}
PERCEPTION_TOOLS = {"MSCN", "InstructSAM", "RemoteSAM", "SM3Det", "SAM2", "ChangeOS"}
DERIVATION_TOOLS = {
    "split_window",
    "lst_single_channel",
    "lst_multi_channel",
    "temperature_emissivity_separation",
    "modis_day_night_lst",
    "ttm_lst",
    "band_ratio",
    "compute_tvdi",
    "ATI",
    "calculate_ndvi",
    "calculate_ndwi",
    "calculate_ndti",
    "calculate_nbr",
    "calculate_water_turbidity_ntu",
    "apply_cloud_mask",
}
THRESHOLD_TOOLS = {
    "calculate_threshold_ratio",
    "calc_threshold_value_mean",
    "calc_batch_image_mean_threshold",
    "count_images_exceeding_threshold_ratio",
    "count_images_exceeding_mean_multiplier",
    "count_pixels_satisfying_conditions",
    "average_ratio_exceeding_threshold",
    "calculate_band_mean_by_condition",
}
SUMMARY_TOOLS = {
    "mean",
    "calc_batch_image_mean",
    "calc_batch_image_mean_mean",
    "calc_batch_image_max",
    "calc_batch_image_sum",
    "calc_batch_image_mean_max_min",
    "calculate_tif_average",
    "get_percentile_value_from_image",
    "max_value_and_index",
    "min_value_and_index",
    "calculate_area",
    "calculate_bbox_area",
    "count_skeleton_contours",
}
COMPARISON_TOOLS = {
    "difference",
    "percentage_change",
    "compute_linear_trend",
    "mann_kendall_test",
    "sens_slope",
    "analyze_hotspot_direction",
    "division",
    "multiply",
    "subtract",
}


def _runtime_doc_path(config: SystemConfig, family_id: str) -> Path:
    return config.docs_root / "runtime_reverse_skills" / f"{family_id}.md"


def _load_detail_from_skill_dir(skill_dir: Path) -> SkillDetail:
    headers = discover_skills(skill_dir.parent)
    for header in headers:
        if Path(header.skill_dir).resolve() == skill_dir.resolve():
            return load_skill_detail(header)
    raise FileNotFoundError(f"Could not load skill detail from {skill_dir}")


def _scan_partial_run_rows(task_local_run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for task_dir in sorted(path for path in task_local_run_dir.glob("task_*") if path.is_dir()):
        task_path = task_dir / "task.json"
        bucket_path = task_dir / "task_bucket.json"
        skill_root = task_dir / "task_local_state" / "skill_library"
        if not task_path.exists() or not bucket_path.exists() or not skill_root.exists():
            continue
        skill_dirs = sorted(path.parent for path in skill_root.glob("*/SKILL.md"))
        if not skill_dirs:
            continue
        task_payload = read_json(task_path)
        bucket_payload = read_json(bucket_path)
        latest_success = False
        for summary_path in sorted(task_dir.glob("iteration_*/iteration_summary.json")):
            try:
                summary = read_json(summary_path)
            except Exception:
                continue
            evaluation = summary.get("evaluation", {})
            if isinstance(evaluation, dict) and evaluation.get("task_success"):
                latest_success = True
        metadata = task_payload.get("metadata", {}) if isinstance(task_payload.get("metadata", {}), dict) else {}
        rows.append(
            {
                "task_id": str(task_payload.get("task_id", "")),
                "original_question_id": str(metadata.get("original_question_id", "")),
                "runtime_family": str(bucket_payload.get("runtime_family", "")),
                "modality": str(bucket_payload.get("modality", "")),
                "training_bucket": str(bucket_payload.get("bucket", "")),
                "prompt": str(task_payload.get("prompt", "")),
                "task_success": latest_success,
                "retained": True,
                "discard_reason": "" if latest_success else "partial_run_latest_skill",
                "final_skill_dir": str(skill_dirs[0].resolve()),
            }
        )
    return rows


def _body_excerpt(detail: SkillDetail, limit: int = 520) -> str:
    text = "\n".join(line.strip() for line in detail.body.splitlines() if line.strip())
    return text[:limit].strip()


def _yaml_list(lines: list[str]) -> str:
    return "\n".join(f"  - {line}" for line in lines)


def _non_resource_tools(detail: SkillDetail) -> list[str]:
    return [tool for tool in detail.header.allowed_tools if tool not in GENERIC_RESOURCE_TOOLS]


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def _body_hint_flags(detail: SkillDetail) -> set[str]:
    text = detail.body.lower()
    flags: set[str] = set()
    if "get_filelist" in text:
        flags.add("file_discovery_first")
    if _contains_any(
        text,
        (
            "returned path",
            "exact returned",
            "actual tes return value",
            "reuse that exact",
            "pass that exact",
            "returned output raster path",
        ),
    ):
        flags.add("reuse_returned_path")
    if _contains_any(text, ("band order", "ascending band order", "preserve band order", "in band order")):
        flags.add("preserve_band_order")
    if _contains_any(text, ("missing", "if any are missing", "if any region is missing", "required inputs")):
        flags.add("validate_inputs")
    if _contains_any(text, ("multiple choice", "nearest matching choice", "answer letter", "closest listed option")):
        flags.add("late_choice_mapping")
    if _contains_any(
        text,
        (
            "signed change",
            "delta =",
            "avg_later",
            "avg_earlier",
            "later - earlier",
            "change between",
            "between two dates",
            "between two dates/months/years",
            "between two time windows",
            "before/after",
        ),
    ):
        flags.add("compare_after_blocks")
    if _contains_any(
        text,
        (
            "benchmark/out",
            "output_paths",
            "relative path",
            "relative paths",
            "task-local output",
            "returned artifact",
        ),
    ):
        flags.add("task_local_output_paths")
    return flags


def _flow_label(tools: list[str]) -> str:
    tool_set = set(tools)
    if tool_set & PERCEPTION_TOOLS:
        if tool_set & (SUMMARY_TOOLS | COMPARISON_TOOLS):
            return "perception -> geometry/area/change summary"
        return "perception -> final answer"
    if tool_set & DERIVATION_TOOLS:
        if tool_set & COMPARISON_TOOLS:
            return "derive per block -> compare/trend"
        if tool_set & THRESHOLD_TOOLS:
            return "derive -> threshold/condition statistic"
        if tool_set & SUMMARY_TOOLS:
            return "derive -> summary statistic"
        return "derive artifact -> consume returned output"
    if tool_set & COMPARISON_TOOLS:
        return "per-block summary -> compare/trend"
    if tool_set & SUMMARY_TOOLS:
        return "collect evidence -> final summary"
    return "inspect inputs -> narrow tool chain -> finalize"


def _pattern_title(row: dict, detail: SkillDetail) -> str:
    bucket = str(row.get("training_bucket", "")).strip() or "general"
    tools = _non_resource_tools(detail)
    if tools:
        return f"{bucket} | {' -> '.join(tools[:3])}"
    return f"{bucket} | {_flow_label(tools)}"


def _pattern_summary(row: dict, detail: SkillDetail) -> str:
    bucket = str(row.get("training_bucket", "")).strip() or "this family"
    tools = _non_resource_tools(detail)
    tool_set = set(tools)
    flags = _body_hint_flags(detail)
    sentences = [
        f"For `{bucket}` prompts, start from actual file grouping and keep the tool chain aligned to the family-specific transform before the final statistic.",
    ]
    if tool_set & DERIVATION_TOOLS:
        derive_tool = next(tool for tool in tools if tool in DERIVATION_TOOLS)
        sentences.append(
            f"Use `{derive_tool}` or the matching family derivation step to create the intermediate artifact first, then feed that returned artifact into downstream tools."
        )
        if tool_set & SUMMARY_TOOLS or tool_set & COMPARISON_TOOLS or "compare_after_blocks" in flags:
            sentences.append(
                "For multi-block workflows with planned derived outputs, finish the producing tool call for every required block before starting summary or comparison steps, and only use tool-returned artifact paths downstream."
            )
        if "task_local_output_paths" in flags:
            sentences.append(
                "Write derived artifacts into a task-local output namespace instead of the source dataset directory, then reuse the exact returned artifact paths downstream."
            )
            sentences.append(
                "Treat helper-planned output paths as seeds only, and treat pre-existing derived files discovered in the source directory as ambient unless the task explicitly names them as required inputs."
            )
    if tool_set & THRESHOLD_TOOLS:
        threshold_tool = next(tool for tool in tools if tool in THRESHOLD_TOOLS)
        sentences.append(
            f"Keep `{threshold_tool}` or the equivalent threshold/count operation near the end, after the target raster or grouped value has been prepared."
        )
    elif tool_set & COMPARISON_TOOLS or "compare_after_blocks" in flags:
        sentences.append(
            "For comparison or trend questions, finish each date, year, or region block first, then run the final compare/trend step."
        )
    elif tool_set & SUMMARY_TOOLS:
        summary_tool = next(tool for tool in tools if tool in SUMMARY_TOOLS)
        sentences.append(
            f"Once grouping is fixed, end with `{summary_tool}` or the matching summary tool rather than stopping at an intermediate raster."
        )
    if "reuse_returned_path" in flags:
        sentences.append("Treat tool-returned paths and values as authoritative, and pass them forward verbatim instead of reconstructing them manually.")
    if "preserve_band_order" in flags:
        sentences.append("When the family depends on ordered bands or scene pairs, preserve canonical order all the way through the derivation step.")
    if "validate_inputs" in flags:
        sentences.append("Validate required bands, scenes, or groups before the expensive derivation step; do not silently guess missing critical inputs.")
    if "late_choice_mapping" in flags:
        sentences.append("If the task is multiple choice, compute the substantive result first and map to the final option only at the end.")
    return " ".join(sentences)


def _build_pattern_rows(source_details: list[tuple[dict, SkillDetail]]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for row, detail in source_details:
        title = _pattern_title(row, detail)
        summary = _pattern_summary(row, detail)
        key = (title, summary)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "summary": summary})
    return rows


def _build_core_guidance(source_details: list[tuple[dict, SkillDetail]]) -> list[str]:
    if not source_details:
        return []
    union_tools = {tool for _, detail in source_details for tool in _non_resource_tools(detail)}
    union_flags = {flag for _, detail in source_details for flag in _body_hint_flags(detail)}
    guidance = [
        "Start from actual file discovery and grouping. Use discovered filenames, dates, regions, and scene pairs as the execution anchor instead of copying assumptions from the prompt.",
    ]
    if union_tools & DERIVATION_TOOLS:
        guidance.append(
            "If the family requires an intermediate artifact or retrieved value, generate it first, write it into a task-local output namespace rather than the source dataset directory, and propagate the exact returned path or value into downstream tools."
        )
        if union_tools & SUMMARY_TOOLS or union_tools & COMPARISON_TOOLS or "compare_after_blocks" in union_flags:
            guidance.append(
                "For multi-block workflows with planned derived outputs, complete the producing tool call for every required block/window before starting batch statistics or comparison, and only use tool-returned artifact paths downstream."
            )
    if union_tools & THRESHOLD_TOOLS:
        guidance.append(
            "For threshold, condition, or exceedance tasks, keep the threshold/count tool near the terminal step, after the target raster or grouped statistic is fully prepared."
        )
    if union_tools & COMPARISON_TOOLS or "compare_after_blocks" in union_flags:
        guidance.append(
            "For multi-date, multi-year, multi-region, or before/after questions, keep one explicit block per unit and only do the final comparison or trend step after those blocks are complete."
        )
    if "compute_linear_trend" in union_tools:
        guidance.append(
            "When using `compute_linear_trend`, prefer `compute_linear_trend(y=series)` and let the observed sequence order act as time by default. Only pass `x` when it is derived from discovered data and exactly matches the `y` length."
        )
    if union_tools & PERCEPTION_TOOLS:
        guidance.append(
            "For RGB families, finish perception or segmentation first, then convert detections into counts, distances, areas, or change summaries."
        )
    if "preserve_band_order" in union_flags:
        guidance.append("When the workflow depends on ordered bands or paired scenes, preserve canonical ordering throughout the derivation stage.")
    if "validate_inputs" in union_flags:
        guidance.append("Validate required bands, scenes, or region groups before the expensive derivation step; do not silently fabricate missing critical inputs.")
    if "late_choice_mapping" in union_flags:
        guidance.append("If the task is multiple choice, compute the substantive result first and only map to the answer option at the end.")
    if "task_local_output_paths" in union_flags:
        guidance.append(
            "If discovery surfaces pre-existing derived artifacts in the source directory, treat them as ambient unless the task explicitly names them; downstream steps for new produced artifacts should use tool-returned paths from the current run."
        )
    deduped: list[str] = []
    for item in guidance:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _build_execution_guidance_md(
    *,
    family_id: str,
    display_name: str,
    core_guidance: list[str],
    pattern_rows: list[dict],
) -> str:
    lines = [
        f"# {display_name} Execution Guidance",
        "",
        "This file is consumer-facing guidance distilled from retained task-local skills.",
        "It intentionally keeps only high-level tool-flow and execution-flow rules, without benchmark question IDs, raw prompt examples, or source skill names.",
        "",
        f"- family_id: `{family_id}`",
        "",
        "## Core Guidance",
    ]
    if core_guidance:
        lines.extend(f"- {item}" for item in core_guidance)
    else:
        lines.append("- No retained task-local guidance was available for this family in the current run.")
    lines.extend(["", "## Abstract Patterns"])
    if pattern_rows:
        for row in pattern_rows:
            lines.extend(
                [
                    f"### {row['title']}",
                    "",
                    row["summary"],
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "No retained task-local pattern was available for this family in the current run.",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("Generated SKILL.md must start with YAML frontmatter.")
    end = normalized.find("\n---\n", 4)
    if end == -1:
        raise ValueError("Generated SKILL.md frontmatter is not closed.")
    meta = yaml.safe_load(normalized[4:end]) or {}
    if not isinstance(meta, dict):
        raise ValueError("Generated SKILL.md frontmatter must be a mapping.")
    body = normalized[end + 5 :].lstrip("\n")
    return meta, body


def _render_skill_markdown(meta: dict, body: str) -> str:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.lstrip()}"


def _normalize_skill_markdown(
    generated_skill_md: str,
    *,
    family_id: str,
    description: str,
    allowed_tools: list[str],
    metadata: dict,
) -> str:
    try:
        parsed_meta, body = _split_frontmatter(generated_skill_md)
    except Exception:
        parsed_meta = {}
        body = generated_skill_md.strip()
    parsed_metadata = parsed_meta.get("metadata", {})
    merged_metadata = dict(parsed_metadata) if isinstance(parsed_metadata, dict) else {}
    merged_metadata.update(metadata)
    normalized_meta = {
        "name": family_id,
        "description": str(parsed_meta.get("description", description)).strip() or description,
        "allowed-tools": allowed_tools,
        "compatibility": "nlrl_skills.executor.task-local-aggregated-v1",
        "metadata": merged_metadata,
    }
    return _render_skill_markdown(normalized_meta, body).strip() + "\n"


def _leakage_markers(
    *,
    family_rows: list[dict],
    source_skill_names: list[str],
    source_ids: list[str] | None = None,
) -> list[str]:
    markers = [
        "source_question_ids",
        "source_skill_names",
        "source_ids",
        "example_questions",
        "original_question_id",
        "source_prompt",
        "source_skill_dir",
    ]
    markers.extend([f"Q{row['original_question_id']}" for row in family_rows if str(row.get("original_question_id", "")).strip()])
    markers.extend(
        f"question {row['original_question_id']}"
        for row in family_rows
        if str(row.get("original_question_id", "")).strip()
    )
    markers.extend(
        f"question_id: {row['original_question_id']}"
        for row in family_rows
        if str(row.get("original_question_id", "")).strip()
    )
    markers.extend(source_skill_names)
    if source_ids:
        markers.extend(source_ids)
    return [marker for marker in markers if marker]


def _find_consumer_leakage_markers(text: str, *, markers: list[str]) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for marker in markers:
        marker_lower = marker.lower()
        if not marker_lower:
            continue
        if marker_lower.isdigit():
            if re.search(rf"\b{re.escape(marker_lower)}\b", lowered):
                matches.append(marker)
            continue
        if marker_lower in lowered:
            matches.append(marker)
    return matches


def _assert_no_consumer_leakage(text: str, *, markers: list[str]) -> None:
    matches = _find_consumer_leakage_markers(text, markers=markers)
    if not matches:
        return
    marker = matches[0]
    if marker.isdigit():
        raise ValueError(f"Consumer-facing aggregation output leaked benchmark identifier: {marker}")
    raise ValueError(f"Consumer-facing aggregation output leaked forbidden marker: {marker}")


def _aggregator_llm_config(config: SystemConfig) -> LLMConfig:
    actor = config.actor
    max_tokens_raw = os.environ.get("NLRL_AGGREGATOR_MAX_TOKENS", "").strip()
    enable_thinking_raw = os.environ.get("NLRL_AGGREGATOR_ENABLE_THINKING", "").strip()
    stream_raw = os.environ.get("NLRL_AGGREGATOR_STREAM", "").strip()
    return LLMConfig(
        name="aggregator",
        model=os.environ.get("NLRL_AGGREGATOR_MODEL", "").strip() or actor.model,
        base_url=os.environ.get("NLRL_AGGREGATOR_BASE_URL", "").strip() or actor.base_url,
        api_key=os.environ.get("NLRL_AGGREGATOR_API_KEY", "").strip() or actor.api_key,
        api_mode=os.environ.get("NLRL_AGGREGATOR_API_MODE", "").strip() or actor.api_mode,
        temperature=float(os.environ.get("NLRL_AGGREGATOR_TEMPERATURE", "").strip() or actor.temperature),
        max_tokens=int(max_tokens_raw) if max_tokens_raw else actor.max_tokens,
        timeout_seconds=int(os.environ.get("NLRL_AGGREGATOR_TIMEOUT_SECONDS", "").strip() or actor.timeout_seconds),
        enable_thinking=(
            enable_thinking_raw.lower() in {"1", "true", "yes", "on"}
            if enable_thinking_raw
            else actor.enable_thinking
        ),
        stream=stream_raw.lower() in {"1", "true", "yes", "on"} if stream_raw else actor.stream,
    )


def _aggregator_prompt_path(config: SystemConfig, default_name: str, env_var: str) -> Path:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return config.prompt_root / default_name
    path = Path(raw)
    if path.is_absolute():
        return path
    return config.prompt_root / raw


def _format_skill_md(
    *,
    family_id: str,
    display_name: str,
    description: str,
    allowed_tools: list[str],
    metadata: dict,
    core_guidance: list[str],
) -> str:
    frontmatter_lines = [
        "---",
        f"name: {family_id}",
        f'description: "{description.replace(chr(34), chr(39))}"',
        "allowed-tools:",
        *[f"  - {tool}" for tool in allowed_tools],
        'compatibility: "nlrl_skills.executor.task-local-aggregated-v1"',
        "metadata:",
    ]
    for key, value in metadata.items():
        if isinstance(value, list):
            frontmatter_lines.append(f"  {key}:")
            for item in value:
                frontmatter_lines.append(f"    - {str(item).replace(chr(34), chr(39))}")
        else:
            frontmatter_lines.append(f'  {key}: "{str(value).replace(chr(34), chr(39))}"')
    frontmatter_lines.extend(["---", ""])

    lines = [
        f"# {display_name}",
        "",
        "## When To Use",
        f"- Use this skill for the `{family_id}` family of Earth observation tasks.",
        f"- {description}",
        "- Treat the routed skill as a strong execution prior, but still inspect the current task wording and files before deciding which concrete tool path to follow.",
        "",
        "## Execution Defaults",
        "- Start from the task payload and file naming evidence. If filenames already expose a derived product, prefer consuming the existing product over recomputing it.",
        "- Use the narrowest reliable tool chain that still preserves benchmark-faithful repeated blocks when the question compares multiple periods, dates, or regions.",
        "- When arithmetic, ranking, or comparison is required, keep the explicit comparison tail instead of stopping at an intermediate statistic.",
        "- If bundled references are needed, inspect `references/RUNTIME_GUIDANCE.md` and `references/EXECUTION_GUIDANCE.md` with `read_file` before guessing.",
        "- If a later answer depends on computed rasters or scalars, use the exact returned paths or values from previous tool outputs rather than reconstructing them manually.",
        "",
        "## Trigger Signals",
        "- Route to this skill when the question wording, filenames, and target outputs align with the metadata route signals and subfamilies in the frontmatter.",
        "- Prefer this skill when its focus tools and source-task patterns match the requested transform, aggregation, perception, or comparison structure better than the other five families.",
        "",
        "## Guided Execution Flow",
    ]
    if core_guidance:
        lines.extend(f"- {item}" for item in core_guidance)
    else:
        lines.append("- No retained task-local source skill was available for this family, so this skill is bootstrapped from the runtime reverse-mapping guidance only.")

    lines.extend(
        [
            "",
            "## Reference Usage",
            "- `references/RUNTIME_GUIDANCE.md` captures the runtime 19.40 reverse-mapped family contract and should be read when a task sits near a routing boundary.",
            "- `references/EXECUTION_GUIDANCE.md` records abstracted high-level tool-flow and execution-flow guidance distilled from retained source skills without exposing benchmark question IDs or raw examples.",
        ]
    )
    return "\n".join(frontmatter_lines + lines).strip() + "\n"


class AggregatedSkillLibraryBuilder:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(_aggregator_llm_config(config))

    def _aggregate_family_with_llm(
        self,
        *,
        family_id: str,
        spec,
        runtime_doc: str,
        allowed_tools: list[str],
        metadata: dict,
        family_rows: list[dict],
        source_details: list[tuple[dict, SkillDetail]],
        core_guidance: list[str],
        pattern_rows: list[dict],
        log_dir: Path,
    ) -> tuple[str, str, dict]:
        source_skill_names = [detail.header.name for _, detail in source_details]
        system_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                "aggregator_system.md",
                "NLRL_AGGREGATOR_SYSTEM_PROMPT",
            )
        )
        user_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                "aggregator_merge_family.md",
                "NLRL_AGGREGATOR_USER_PROMPT",
            ),
            family_json=json.dumps(
                {
                    "family_id": family_id,
                    "display_name": spec.display_name,
                    "description": spec.description,
                    "domain": FAMILY_TO_DOMAIN[family_id],
                    "benchmark_question_range_prior": list(spec.question_range),
                    "route_signals": sorted(set(spec.trigger_keywords)),
                    "training_buckets": sorted({row["training_bucket"] for row in family_rows}),
                    "allowed_tools_candidate": allowed_tools,
                    "allowed_tools_candidate_semantics": (
                        "Treat `allowed_tools_candidate` as the hard executable envelope for this aggregated skill. "
                        "It must remain tight, but it also must be sufficient for end-to-end execution so the executor "
                        "does not dead-end mid-run after routing into the family."
                    ),
                    "requested_focus_mode_contract": (
                        "Add a lightweight in-envelope focus layer for 2-4 family-internal modes. "
                        "Each mode should only help the executor choose tools inside the allowed envelope; "
                        "do not recreate question-level routing or an external shortlist."
                    ),
                    "consumer_metadata_contract": metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
            runtime_guidance_md=runtime_doc,
            distilled_guidance_json=json.dumps(
                {
                    "core_guidance_hints": core_guidance,
                    "abstract_pattern_hints": pattern_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            source_skills_json=json.dumps(
                [
                    {
                        "training_bucket": str(row["training_bucket"]),
                        "header": {
                            "name": detail.header.name,
                            "description": detail.header.description,
                            "allowed_tools": detail.header.allowed_tools,
                        },
                        "body": detail.body,
                        "resources": detail.resources,
                    }
                    for row, detail in source_details
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "aggregator_merge_family", llm_result)
        description = str(payload.get("description", spec.description)).strip() or spec.description
        skill_md_raw = str(payload.get("skill_md", "")).strip()
        guidance_md = str(payload.get("execution_guidance_md", "")).strip()
        if not skill_md_raw:
            raise ValueError(f"Aggregator LLM returned empty skill_md for {family_id}")
        if not guidance_md:
            raise ValueError(f"Aggregator LLM returned empty execution_guidance_md for {family_id}")
        skill_md = _normalize_skill_markdown(
            skill_md_raw,
            family_id=family_id,
            description=description,
            allowed_tools=allowed_tools,
            metadata=metadata,
        )
        guidance_md = guidance_md.strip() + "\n"
        markers = _leakage_markers(family_rows=family_rows, source_skill_names=source_skill_names)
        _assert_no_consumer_leakage(skill_md, markers=markers)
        _assert_no_consumer_leakage(guidance_md, markers=markers)
        return skill_md, guidance_md, payload

    def build_from_run(
        self,
        task_local_run_dir: Path,
        *,
        output_root: Path | None = None,
    ) -> Path:
        task_local_run_dir = task_local_run_dir.resolve()
        retained_path = task_local_run_dir / "retained_task_skills.json"
        source_mode = "retained_task_skills.json"
        if retained_path.exists():
            retained_rows = read_json(retained_path)
            if not retained_rows:
                retained_rows = _scan_partial_run_rows(task_local_run_dir)
                source_mode = "partial_run_scan_after_empty_retained"
        else:
            retained_rows = _scan_partial_run_rows(task_local_run_dir)
            source_mode = "partial_run_scan"
        if not retained_rows:
            raise FileNotFoundError(f"No aggregatable task-local skills found under {task_local_run_dir}")
        output_root = (output_root or (task_local_run_dir / "aggregated_skill_library")).resolve()

        if output_root.exists():
            for child in output_root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        ensure_dir(output_root)

        by_family: dict[str, list[dict]] = {family_id: [] for family_id in SKILL_SPECS}
        for row in retained_rows:
            family_id = str(row.get("runtime_family", "")).strip()
            if family_id in by_family:
                by_family[family_id].append(row)

        aggregation_summary = {
            "source_run_dir": str(task_local_run_dir),
            "source_mode": source_mode,
            "output_root": str(output_root),
            "families": [],
        }

        for family_id, spec in SKILL_SPECS.items():
            family_rows = sorted(
                by_family.get(family_id, []),
                key=lambda item: int(str(item.get("original_question_id", "0"))),
            )
            source_details: list[tuple[dict, SkillDetail]] = []
            source_skill_names: list[str] = []
            for row in family_rows:
                skill_dir = Path(row["final_skill_dir"])
                detail = _load_detail_from_skill_dir(skill_dir)
                source_details.append((row, detail))
                source_skill_names.append(detail.header.name)

            runtime_doc = read_text(_runtime_doc_path(self.config, family_id))
            allowed_tools = sorted(
                {
                    *spec.tool_allowlist,
                    *[
                        tool
                        for _, detail in source_details
                        for tool in detail.header.allowed_tools
                    ],
                }
            )
            metadata = {
                "family_id": family_id,
                "display_name": spec.display_name,
                "domain": FAMILY_TO_DOMAIN[family_id],
                "benchmark_question_range_prior": f"{spec.question_range[0]}-{spec.question_range[1]}",
                "route_signals": sorted(set(spec.trigger_keywords)),
                "training_buckets": sorted({row["training_bucket"] for row in family_rows}),
            }
            pattern_rows = _build_pattern_rows(source_details)
            core_guidance = _build_core_guidance(source_details)
            skill_md, execution_guidance_md, llm_payload = self._aggregate_family_with_llm(
                family_id=family_id,
                spec=spec,
                runtime_doc=runtime_doc,
                allowed_tools=allowed_tools,
                metadata=metadata,
                family_rows=family_rows,
                source_details=source_details,
                core_guidance=core_guidance,
                pattern_rows=pattern_rows,
                log_dir=output_root / "_aggregation_logs" / slugify(family_id),
            )

            aggregation_info = {
                "family_id": family_id,
                "display_name": spec.display_name,
                "domain": FAMILY_TO_DOMAIN[family_id],
                "source_mode": source_mode,
                "retained_source_count": len(family_rows),
                "training_buckets": sorted({row["training_bucket"] for row in family_rows}),
                "source_question_ids": [str(row["original_question_id"]) for row in family_rows],
                "source_skill_names": source_skill_names,
                "sources": [
                    {
                        "original_question_id": str(row["original_question_id"]),
                        "training_bucket": str(row["training_bucket"]),
                        "skill_name": detail.header.name,
                        "skill_dir": str(row["final_skill_dir"]),
                    }
                    for row, detail in source_details
                ],
                "llm_summary": str(llm_payload.get("summary", "")),
            }
            aggregation_info_path = output_root / "_aggregation_info" / f"{slugify(family_id)}.json"
            write_json(aggregation_info_path, aggregation_info)

            write_skill_bundle(
                output_root,
                family_id,
                {
                    "SKILL.md": skill_md,
                    "references/RUNTIME_GUIDANCE.md": runtime_doc,
                    "references/EXECUTION_GUIDANCE.md": execution_guidance_md,
                },
            )

            aggregation_summary["families"].append(
                {
                    "family_id": family_id,
                    "display_name": spec.display_name,
                    "domain": FAMILY_TO_DOMAIN[family_id],
                    "retained_source_count": len(family_rows),
                    "source_question_ids": [str(row["original_question_id"]) for row in family_rows],
                    "source_skill_names": source_skill_names,
                    "aggregation_info_path": str(aggregation_info_path.resolve()),
                    "output_skill_dir": str((output_root / slugify(family_id)).resolve()),
                }
            )

        write_json(output_root / "aggregation_summary.json", aggregation_summary)
        return output_root
