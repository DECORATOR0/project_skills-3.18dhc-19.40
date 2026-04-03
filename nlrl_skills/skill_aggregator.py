from __future__ import annotations

import shutil
from pathlib import Path

from agent.skill_eval.skill_router import SKILL_SPECS

from .config import SystemConfig
from .schemas import SkillDetail
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


def _format_skill_md(
    *,
    family_id: str,
    display_name: str,
    description: str,
    allowed_tools: list[str],
    metadata: dict,
    source_rows: list[dict],
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
        "- If bundled references are needed, inspect `references/RUNTIME_GUIDANCE.md` and `references/SOURCE_TASKS.md` with `read_file` before guessing.",
        "- If a later answer depends on computed rasters or scalars, use the exact returned paths or values from previous tool outputs rather than reconstructing them manually.",
        "",
        "## Trigger Signals",
        "- Route to this skill when the question wording, filenames, and target outputs align with the metadata route signals and subfamilies in the frontmatter.",
        "- Prefer this skill when its focus tools and source-task patterns match the requested transform, aggregation, perception, or comparison structure better than the other five families.",
        "",
        "## Learned Workflow Patterns",
    ]
    if source_rows:
        for row in source_rows:
            lines.extend(
                [
                    f"- Q{row['original_question_id']} | bucket={row['training_bucket']} | source={row['skill_name']}",
                    f"  {row['summary']}",
                ]
            )
    else:
        lines.append("- No retained task-local source skill was available for this family, so this skill is bootstrapped from the runtime reverse-mapping guidance only.")

    lines.extend(
        [
            "",
            "## Reference Usage",
            "- `references/RUNTIME_GUIDANCE.md` captures the runtime 19.40 reverse-mapped family contract and should be read when a task sits near a routing boundary.",
            "- `references/SOURCE_TASKS.md` records the retained training tasks, their buckets, and short source-skill summaries so the executor can inspect concrete exemplars when needed.",
        ]
    )
    return "\n".join(frontmatter_lines + lines).strip() + "\n"


class AggregatedSkillLibraryBuilder:
    def __init__(self, config: SystemConfig):
        self.config = config

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
                "source_question_ids": [str(row["original_question_id"]) for row in family_rows],
                "source_skill_names": source_skill_names,
                "example_questions": [str(row["prompt"])[:180] for row in family_rows[:4]],
            }
            source_rows = [
                {
                    "original_question_id": str(row["original_question_id"]),
                    "training_bucket": str(row["training_bucket"]),
                    "skill_name": detail.header.name,
                    "summary": _body_excerpt(detail),
                }
                for row, detail in source_details
            ]
            skill_md = _format_skill_md(
                family_id=family_id,
                display_name=spec.display_name,
                description=spec.description,
                allowed_tools=allowed_tools,
                metadata=metadata,
                source_rows=source_rows,
            )

            source_task_lines = [
                f"# {spec.display_name} Source Tasks",
                "",
                f"- family_id: `{family_id}`",
                f"- retained_task_count: `{len(family_rows)}`",
                "",
            ]
            if family_rows:
                for row, detail in source_details:
                    source_task_lines.extend(
                        [
                            f"## Q{row['original_question_id']} | {row['training_bucket']} | {detail.header.name}",
                            "",
                            f"- source_skill_dir: `{row['final_skill_dir']}`",
                            f"- source_prompt: {row['prompt']}",
                            "",
                            "### Summary",
                            "",
                            _body_excerpt(detail, limit=900),
                            "",
                        ]
                    )
            else:
                source_task_lines.extend(
                    [
                        "No retained task-local skill was available for this family in the current run.",
                        "",
                        "This aggregated skill is therefore bootstrapped entirely from the runtime reverse-mapping guide.",
                        "",
                    ]
                )

            write_skill_bundle(
                output_root,
                family_id,
                {
                    "SKILL.md": skill_md,
                    "references/RUNTIME_GUIDANCE.md": runtime_doc,
                    "references/SOURCE_TASKS.md": "\n".join(source_task_lines).strip() + "\n",
                },
            )

            aggregation_summary["families"].append(
                {
                    "family_id": family_id,
                    "display_name": spec.display_name,
                    "domain": FAMILY_TO_DOMAIN[family_id],
                    "retained_source_count": len(family_rows),
                    "source_question_ids": [str(row["original_question_id"]) for row in family_rows],
                    "output_skill_dir": str((output_root / slugify(family_id)).resolve()),
                }
            )

        write_json(output_root / "aggregation_summary.json", aggregation_summary)
        return output_root
