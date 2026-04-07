from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import LLMMessage, SkillDetail
from .skill_aggregator import (
    _aggregator_llm_config,
    _aggregator_prompt_path,
    _assert_no_consumer_leakage,
    _build_core_guidance,
    _build_pattern_rows,
    _leakage_markers,
    _load_detail_from_skill_dir,
    _render_skill_markdown,
    _scan_partial_run_rows,
    _split_frontmatter,
)
from .skills import write_skill_bundle
from .utils import ensure_dir, read_json, slugify, utc_timestamp, write_json

_PUBLIC_NAME_ACRONYMS = {"ndti", "ndvi", "ndwi", "ndsi", "ndbi", "evi", "nbr", "fvc", "lst", "tvdi", "ati", "pwv"}


@dataclass
class ClusterSource:
    source_id: str
    row: dict
    detail: SkillDetail


def _sort_key(row: dict) -> tuple[int, str]:
    raw = str(row.get("original_question_id", "")).strip()
    if raw.isdigit():
        return (0, f"{int(raw):06d}")
    return (1, raw)


def _load_retained_rows(task_local_run_dir: Path) -> tuple[list[dict], str]:
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
    retained_rows = sorted(retained_rows, key=_sort_key)
    return retained_rows, source_mode


def _trim(text: str, limit: int) -> str:
    cleaned = " ".join(part.strip() for part in str(text).splitlines() if part.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _unique_source_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    value = f"{base}-{suffix}"
    used.add(value)
    return value


def _cluster_source_payload(source: ClusterSource) -> dict:
    return {
        "source_id": source.source_id,
        "task_prompt": _trim(source.row.get("prompt", ""), 320),
        "source_skill": {
            "name": source.detail.header.name,
            "description": source.detail.header.description,
            "allowed_tools": source.detail.header.allowed_tools,
        },
        "skill_body_excerpt": _trim(source.detail.body, 900),
    }


def _normalize_cluster_output(payload: dict, *, source_lookup: dict[str, ClusterSource], cluster_count: int) -> list[dict]:
    clusters_raw = payload.get("clusters", [])
    if not isinstance(clusters_raw, list):
        raise ValueError("Clusterer must return a top-level `clusters` list.")
    if len(clusters_raw) != cluster_count:
        raise ValueError(f"Clusterer returned {len(clusters_raw)} clusters; expected {cluster_count}.")
    assigned: list[str] = []
    normalized: list[dict] = []
    used_name_slugs: set[str] = set()
    for index, cluster in enumerate(clusters_raw, start=1):
        if not isinstance(cluster, dict):
            raise ValueError("Each cluster entry must be a JSON object.")
        name = str(cluster.get("name", "")).strip() or f"Auto Cluster {index}"
        description = str(cluster.get("description", "")).strip() or f"Cluster {index} aggregated skill."
        source_ids = cluster.get("source_ids", cluster.get("member_source_ids", []))
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError(f"Cluster {index} must contain a non-empty `source_ids` list.")
        cleaned_source_ids: list[str] = []
        for raw_id in source_ids:
            source_id = str(raw_id).strip()
            if not source_id:
                continue
            if source_id not in source_lookup:
                raise ValueError(f"Cluster {index} referenced unknown source_id: {source_id}")
            cleaned_source_ids.append(source_id)
        if not cleaned_source_ids:
            raise ValueError(f"Cluster {index} contains no valid source ids.")
        assigned.extend(cleaned_source_ids)
        name_slug = slugify(name) or f"cluster-{index}"
        if name_slug in used_name_slugs:
            name = f"{name} {index}"
            name_slug = slugify(name) or f"cluster-{index}"
        used_name_slugs.add(name_slug)
        normalized.append(
            {
                "cluster_id": f"cluster_{index:02d}",
                "name": name,
                "name_slug": name_slug,
                "description": description,
                "source_ids": cleaned_source_ids,
                "rationale": str(cluster.get("rationale", "")).strip(),
            }
        )
    assigned_set = set(assigned)
    expected_set = set(source_lookup)
    if assigned_set != expected_set or len(assigned) != len(expected_set):
        missing = sorted(expected_set - assigned_set)
        duplicate_count = len(assigned) - len(assigned_set)
        raise ValueError(
            "Cluster assignment must cover each source exactly once. "
            f"missing={missing} duplicate_count={duplicate_count}"
        )
    return normalized


def _normalize_skill_markdown_v2(
    generated_skill_md: str,
    *,
    name: str,
    description: str,
    allowed_tools: list[str],
    compatibility: str,
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
        "name": str(parsed_meta.get("name", name)).strip() or name,
        "description": str(parsed_meta.get("description", description)).strip() or description,
        "allowed-tools": allowed_tools,
        "compatibility": compatibility,
        "metadata": merged_metadata,
    }
    return _render_skill_markdown(normalized_meta, body).strip() + "\n"


def _assert_required_sections(text: str, headings: list[str], *, label: str) -> None:
    for heading in headings:
        if heading not in text:
            raise ValueError(f"{label} is missing required heading: {heading}")


def _allowed_tools_from_sources(sources: list[ClusterSource]) -> list[str]:
    return sorted(
        {
            tool
            for source in sources
            for tool in source.detail.header.allowed_tools
        }
    )


def _collect_shared_resource_bundle(sources: list[ClusterSource]) -> tuple[dict[str, str], list[str]]:
    bundled: dict[str, str] = {}
    conflicts: set[str] = set()
    for source in sources:
        skill_dir = Path(source.detail.header.skill_dir)
        for relative_path in source.detail.resources:
            resource_path = skill_dir / relative_path
            if not resource_path.is_file():
                continue
            try:
                content = resource_path.read_text(encoding="utf-8")
            except Exception:
                continue
            existing = bundled.get(relative_path)
            if existing is None:
                bundled[relative_path] = content
                continue
            if existing != content:
                conflicts.add(relative_path)
    for relative_path in conflicts:
        bundled.pop(relative_path, None)
    return bundled, sorted(conflicts)


def _augment_routing_surface(
    *,
    name: str,
    description: str,
    allowed_tools: list[str],
    sources: list[ClusterSource],
    shape: str,
) -> tuple[str, str]:
    source_text = " ".join(
        part
        for source in sources
        for part in (
            str(source.row.get("prompt", "")),
            source.detail.header.name,
            source.detail.header.description,
            source.detail.body,
        )
        if part
    ).lower()
    normalized_name = name.strip()
    normalized_description = description.strip()
    routing_surface = f"{normalized_name} {normalized_description}".lower()

    if "calculate_batch_ndti" in allowed_tools:
        if "ndti" in source_text and "ndti" not in routing_surface:
            if "ndti" not in normalized_name.lower():
                suffix = "-tree" if shape == "tree" else ""
                normalized_name = f"ndti-change-between-periods{suffix}"
            normalized_description = (
                "Handle NDTI/turbidity change by pairing the required bands, "
                "summarizing explicit before/after periods, and comparing those period summaries."
            )
        elif not any(token in routing_surface for token in ("paired band", "paired-band", "before/after", "period")):
            normalized_description = (
                normalized_description.rstrip(".")
                + "; use it for paired-band index change between explicit periods rather than generic linear trend."
            )
        if not any(token in routing_surface for token in ("same month", "across years", "cross-year", "month/year", "two named years")):
            normalized_description = (
                normalized_description.rstrip(".")
                + " It also fits same-month-across-years or two-named-year comparisons when the task asks for before/after change rather than linear trend."
            )

    if "compute_linear_trend" in allowed_tools and not any(
        token in routing_surface for token in ("linear trend", "slope", "regression")
    ):
        normalized_description = (
            normalized_description.rstrip(".")
            + "; use it for linear trend / slope estimation over one ordered raster time series."
        )

    return normalized_name, normalized_description


def _normalize_public_skill_name(name: str) -> str:
    cleaned = " ".join(str(name).strip().split())
    if not cleaned or " " in cleaned or "-" not in cleaned:
        return cleaned
    parts = [part for part in re.split(r"[-_]+", cleaned) if part]
    if not parts:
        return cleaned
    normalized_parts = [
        part.upper() if part.lower() in _PUBLIC_NAME_ACRONYMS else part
        for part in parts
    ]
    return " ".join(normalized_parts)


class V8AggregatedSkillLibraryBuilder:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(_aggregator_llm_config(config))

    def _load_sources(self, task_local_run_dir: Path) -> tuple[list[ClusterSource], str]:
        retained_rows, source_mode = _load_retained_rows(task_local_run_dir)
        used_ids: set[str] = set()
        sources: list[ClusterSource] = []
        for row in retained_rows:
            skill_dir = Path(row["final_skill_dir"])
            detail = _load_detail_from_skill_dir(skill_dir)
            question_id = str(row.get("original_question_id", "")).strip() or str(row.get("task_id", "")).strip()
            skill_slug = slugify(detail.header.name) or "source-skill"
            base_source_id = f"q{question_id}-{skill_slug}" if question_id else skill_slug
            source_id = _unique_source_id(base_source_id, used_ids)
            sources.append(ClusterSource(source_id=source_id, row=row, detail=detail))
        return sources, source_mode

    def _cluster_sources(
        self,
        *,
        sources: list[ClusterSource],
        cluster_count: int,
        log_dir: Path,
    ) -> list[dict]:
        source_payload = [_cluster_source_payload(source) for source in sources]
        system_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                "aggregator_cluster_system_v8.md",
                "NLRL_AGGREGATOR_V8_CLUSTER_SYSTEM_PROMPT",
            )
        )
        user_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                "aggregator_cluster_user_v8.md",
                "NLRL_AGGREGATOR_V8_CLUSTER_USER_PROMPT",
            ),
            cluster_count=cluster_count,
            sources_json=json.dumps(source_payload, ensure_ascii=False, indent=2),
        )
        source_lookup = {source.source_id: source for source in sources}
        last_error: Exception | None = None
        for attempt in range(1, 4):
            attempt_user_prompt = user_prompt
            if last_error is not None:
                attempt_user_prompt += (
                    "\n\nPrevious output was invalid and must be corrected.\n"
                    f"Validation error: {last_error}\n"
                    "Return fresh JSON that covers every source_id exactly once, "
                    "with no missing ids and no duplicates."
                )
            payload, llm_result = self.llm.chat_json(
                [
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=attempt_user_prompt),
                ]
            )
            log_llm_call(log_dir, f"aggregator_cluster_v8_attempt_{attempt}", llm_result)
            try:
                return _normalize_cluster_output(payload, source_lookup=source_lookup, cluster_count=cluster_count)
            except Exception as exc:
                last_error = exc
                if attempt >= 3:
                    raise
        raise RuntimeError("Clustering failed without raising a concrete validation error.")

    def _aggregate_cluster_shape(
        self,
        *,
        shape: str,
        cluster: dict,
        sources: list[ClusterSource],
        branch_root: Path,
        info_root: Path,
        log_root: Path,
        source_mode: str,
    ) -> dict:
        cluster_rows = [source.row for source in sources]
        source_details = [(source.row, source.detail) for source in sources]
        source_skill_names = [source.detail.header.name for source in sources]
        shared_resource_bundle, shared_resource_conflicts = _collect_shared_resource_bundle(sources)
        allowed_tools = _allowed_tools_from_sources(sources)
        if not any(path.startswith("scripts/") for path in shared_resource_bundle):
            allowed_tools = [tool for tool in allowed_tools if tool != "run_python_script"]
        if not any(path.startswith("references/") for path in shared_resource_bundle):
            allowed_tools = [tool for tool in allowed_tools if tool != "read_file"]
        metadata = {
            "schema_version": "aggregated_skill_v2",
            "skill_shape": shape,
            "aggregation_model": self.llm.config.model,
            "source_skill_count": len(source_skill_names),
            "source_task_count": len(cluster_rows),
            "generated_at": utc_timestamp(),
            "shared_resource_count": len(shared_resource_bundle),
        }
        compatibility = f"nlrl_skills.executor.aggregated-v2-{shape}"
        core_guidance = _build_core_guidance(source_details)
        pattern_rows = _build_pattern_rows(source_details)
        system_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                f"aggregator_{shape}_system_v8.md",
                f"NLRL_AGGREGATOR_V8_{shape.upper()}_SYSTEM_PROMPT",
            )
        )
        user_prompt = render_prompt(
            _aggregator_prompt_path(
                self.config,
                f"aggregator_{shape}_user_v8.md",
                f"NLRL_AGGREGATOR_V8_{shape.upper()}_USER_PROMPT",
            ),
            cluster_json=json.dumps(
                {
                    "cluster_id": cluster["cluster_id"],
                    "name": cluster["name"],
                    "description": cluster["description"],
                    "allowed_tools_candidate": allowed_tools,
                    "allowed_tools_candidate_semantics": (
                        "Treat `allowed_tools_candidate` as the hard executable envelope for this aggregated skill. "
                        "Keep it tight, but still sufficient for end-to-end execution after routing."
                    ),
                    "shared_resource_candidates": sorted(shared_resource_bundle),
                    "shared_resource_conflicts": shared_resource_conflicts,
                    "shared_resource_candidate_semantics": (
                        "Any path listed in `shared_resource_candidates` will be bundled unchanged in the final aggregated skill. "
                        "If a listed helper script or reference materially reduces executor guesswork while staying reusable, "
                        "you may reference that exact path in the generated guidance."
                    ),
                    "consumer_metadata_contract": metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
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
                        "source_id": source.source_id,
                        "task_prompt": str(source.row.get("prompt", "")),
                        "header": {
                            "name": source.detail.header.name,
                            "description": source.detail.header.description,
                            "allowed_tools": source.detail.header.allowed_tools,
                        },
                        "body": source.detail.body,
                        "resources": source.detail.resources,
                    }
                    for source in sources
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
        log_llm_call(log_root, f"aggregator_{shape}_v8", llm_result)

        generated_name = str(payload.get("name", "")).strip() or cluster["name"]
        generated_description = str(payload.get("description", "")).strip() or cluster["description"]
        generated_name, generated_description = _augment_routing_surface(
            name=generated_name,
            description=generated_description,
            allowed_tools=allowed_tools,
            sources=sources,
            shape=shape,
        )
        generated_name = _normalize_public_skill_name(generated_name)
        skill_md_raw = str(payload.get("skill_md", "")).strip()
        if not skill_md_raw:
            raise ValueError(f"{shape} aggregation returned empty `skill_md` for {cluster['cluster_id']}")
        skill_md = _normalize_skill_markdown_v2(
            skill_md_raw,
            name=generated_name,
            description=generated_description,
            allowed_tools=allowed_tools,
            compatibility=compatibility,
            metadata=metadata,
        )
        if shape == "flat":
            _assert_required_sections(
                skill_md,
                [
                    "## High-Level Execution Guidance",
                    "## Common Defaults",
                    "## Global Guardrails",
                ],
                label=f"{cluster['cluster_id']} flat SKILL.md",
            )
            bundle_files = {"SKILL.md": skill_md}
            for relative_path, content in shared_resource_bundle.items():
                if relative_path not in bundle_files:
                    bundle_files[relative_path] = content
            guidance_md = ""
        else:
            guidance_md = str(payload.get("execution_guidance_md", "")).strip()
            if not guidance_md:
                raise ValueError(f"{shape} aggregation returned empty `execution_guidance_md` for {cluster['cluster_id']}")
            _assert_required_sections(
                skill_md,
                [
                    "## Execution Profile Index",
                    "## Global Execution Rules",
                    "## Global Guardrails",
                    "## Reference Usage",
                ],
                label=f"{cluster['cluster_id']} tree SKILL.md",
            )
            bundle_files = {
                "SKILL.md": skill_md,
                "references/EXECUTION_GUIDANCE.md": guidance_md.strip() + "\n",
            }
            for relative_path, content in shared_resource_bundle.items():
                if relative_path not in bundle_files:
                    bundle_files[relative_path] = content

        markers = _leakage_markers(family_rows=cluster_rows, source_skill_names=source_skill_names)
        _assert_no_consumer_leakage(skill_md, markers=markers)
        if guidance_md:
            _assert_no_consumer_leakage(guidance_md, markers=markers)

        skill_dir = write_skill_bundle(branch_root, generated_name, bundle_files)
        info = {
            "shape": shape,
            "cluster_id": cluster["cluster_id"],
            "cluster_name": cluster["name"],
            "generated_skill_name": generated_name,
            "description": generated_description,
            "source_mode": source_mode,
            "source_ids": [source.source_id for source in sources],
            "source_skill_names": source_skill_names,
            "source_question_ids": [str(source.row.get("original_question_id", "")) for source in sources],
            "allowed_tools": allowed_tools,
            "llm_summary": str(payload.get("summary", "")).strip(),
            "output_skill_dir": str(skill_dir.resolve()),
        }
        info_path = info_root / f"{shape}__{cluster['name_slug']}.json"
        write_json(info_path, info)
        return info | {"aggregation_info_path": str(info_path.resolve())}

    def build_from_run(
        self,
        task_local_run_dir: Path,
        *,
        output_root: Path | None = None,
        cluster_count: int = 6,
        aggregation_concurrency: int = 30,
    ) -> Path:
        task_local_run_dir = task_local_run_dir.resolve()
        sources, source_mode = self._load_sources(task_local_run_dir)
        requested_cluster_count = max(1, int(cluster_count))
        actual_cluster_count = min(requested_cluster_count, len(sources))
        output_root = (output_root or (task_local_run_dir / "aggregated_skill_library_v8")).resolve()

        if output_root.exists():
            for child in output_root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        ensure_dir(output_root)
        flat_root = ensure_dir(output_root / "flat")
        tree_root = ensure_dir(output_root / "tree")
        info_root = ensure_dir(output_root / "_aggregation_info")
        clustering_log_root = ensure_dir(output_root / "_aggregation_logs" / "clustering")

        clusters = self._cluster_sources(
            sources=sources,
            cluster_count=actual_cluster_count,
            log_dir=clustering_log_root,
        )
        source_lookup = {source.source_id: source for source in sources}
        cluster_assignment = {
            "source_run_dir": str(task_local_run_dir),
            "source_mode": source_mode,
            "requested_cluster_count": requested_cluster_count,
            "actual_cluster_count": actual_cluster_count,
            "source_count": len(sources),
            "clusters": clusters,
        }
        write_json(output_root / "cluster_assignment.json", cluster_assignment)

        branch_results: dict[str, list[dict]] = {"flat": [], "tree": []}
        future_map = {}
        with ThreadPoolExecutor(max_workers=max(1, aggregation_concurrency)) as executor:
            for shape, branch_root in (("flat", flat_root), ("tree", tree_root)):
                for cluster in clusters:
                    cluster_sources = [source_lookup[source_id] for source_id in cluster["source_ids"]]
                    future = executor.submit(
                        self._aggregate_cluster_shape,
                        shape=shape,
                        cluster=cluster,
                        sources=cluster_sources,
                        branch_root=branch_root,
                        info_root=info_root,
                        log_root=ensure_dir(output_root / "_aggregation_logs" / shape / cluster["name_slug"]),
                        source_mode=source_mode,
                    )
                    future_map[future] = shape
            for future in as_completed(future_map):
                shape = future_map[future]
                branch_results[shape].append(future.result())

        for shape in branch_results:
            branch_results[shape].sort(key=lambda item: item["cluster_id"])

        aggregation_summary = {
            "source_run_dir": str(task_local_run_dir),
            "source_mode": source_mode,
            "output_root": str(output_root),
            "requested_cluster_count": requested_cluster_count,
            "actual_cluster_count": actual_cluster_count,
            "source_count": len(sources),
            "branches": {
                "flat": {
                    "skill_library_root": str(flat_root),
                    "clusters": branch_results["flat"],
                },
                "tree": {
                    "skill_library_root": str(tree_root),
                    "clusters": branch_results["tree"],
                },
            },
        }
        write_json(output_root / "aggregation_summary.json", aggregation_summary)
        return output_root
