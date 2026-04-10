from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from .schemas import ExperienceEntry, SkillDetail, SkillHeader, SkillPhase, to_dict
from .utils import append_jsonl, ensure_dir, read_text, relative_to, safe_relative_path, slugify, write_text


def _split_frontmatter(full_text: str) -> tuple[dict[str, Any], str]:
    text = full_text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter.")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter is not closed.")
    frontmatter = text[4:end]
    body = text[end + 5 :].lstrip("\n")
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must parse to a mapping.")
    return parsed, body


def _loose_frontmatter_parse(frontmatter: str) -> dict[str, Any]:
    yaml_section_keys = {"allowed-tools", "allowed_tools", "metadata", "parameters", "references", "references/", "scripts", "scripts/"}
    try:
        parsed = yaml.safe_load(frontmatter) or {}
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    parsed: dict[str, Any] = {}
    current_key: str | None = None
    current_value: list[str] = []
    block_scalar = False

    def flush() -> None:
        nonlocal current_key, current_value, block_scalar
        if current_key is None:
            return
        value = "\n".join(current_value).strip()
        if current_key in yaml_section_keys:
            try:
                parsed[current_key] = yaml.safe_load(value) if value else None
            except Exception:
                parsed[current_key] = value
        elif value:
            parsed[current_key] = value
        elif current_key not in parsed:
            parsed[current_key] = ""
        current_key = None
        current_value = []
        block_scalar = False

    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^([A-Za-z0-9_./-]+):\s*(.*)$", line)
        if match and not raw_line.startswith((" ", "\t")):
            flush()
            current_key = match.group(1)
            tail = match.group(2)
            if tail in {"|", "|-", ">", ">-"}:
                block_scalar = True
                current_value = []
            elif tail:
                current_value = [tail]
            else:
                current_value = []
            continue
        bare_section = re.match(r"^([A-Za-z0-9_./-]+/)\s*$", line)
        if bare_section and not raw_line.startswith((" ", "\t")):
            flush()
            current_key = bare_section.group(1)
            current_value = []
            block_scalar = True
            continue
        if current_key is None:
            continue
        if block_scalar:
            current_value.append(raw_line[2:] if raw_line.startswith("  ") else raw_line)
        elif raw_line.strip():
            if current_key in yaml_section_keys and raw_line.startswith("  "):
                current_value.append(raw_line[2:])
            else:
                current_value.append(raw_line.strip())
    flush()
    return parsed


def discover_skills(skill_library_root: Path) -> list[SkillHeader]:
    ensure_dir(skill_library_root)
    headers: list[SkillHeader] = []
    for skill_md in sorted(skill_library_root.glob("*/SKILL.md")):
        try:
            full_text = read_text(skill_md)
            meta, _ = _split_frontmatter(full_text)
            allowed_raw = meta.get("allowed-tools", "")
            if isinstance(allowed_raw, list):
                allowed_tools = [str(item).strip() for item in allowed_raw if str(item).strip()]
            elif isinstance(allowed_raw, str):
                allowed_tools = [item for item in allowed_raw.split() if item]
            else:
                allowed_tools = []
            headers.append(
                SkillHeader(
                    name=str(meta.get("name", skill_md.parent.name)),
                    description=str(meta.get("description", "")).strip(),
                    skill_dir=str(skill_md.parent.resolve()),
                    skill_md_path=str(skill_md.resolve()),
                    compatibility=str(meta.get("compatibility", "")).strip(),
                    allowed_tools=allowed_tools,
                    metadata=meta.get("metadata", {}) if isinstance(meta.get("metadata", {}), dict) else {},
                )
            )
        except Exception:
            continue
    return headers


_PHASE_HEADER_RE = re.compile(r"^##\s+Phase:\s*(\S+)", re.IGNORECASE)


def parse_skill_phases(body: str) -> dict[str, SkillPhase]:
    """Extract ``## Phase: NAME`` sections from the SKILL.md body."""
    lines = body.splitlines(keepends=True)
    phases: dict[str, SkillPhase] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    order = 0

    def _flush() -> None:
        nonlocal current_name, current_lines, order
        if current_name is not None:
            phases[current_name] = SkillPhase(
                name=current_name,
                content="".join(current_lines).strip(),
                order=order,
            )
            order += 1
            current_lines = []
            current_name = None

    for line in lines:
        match = _PHASE_HEADER_RE.match(line)
        if match:
            _flush()
            current_name = match.group(1).strip().upper()
            continue
        if current_name is not None:
            current_lines.append(line)
    _flush()
    return phases


def load_skill_detail(header: SkillHeader) -> SkillDetail:
    skill_md = Path(header.skill_md_path)
    full_text = read_text(skill_md)
    _, body = _split_frontmatter(full_text)
    phases = parse_skill_phases(body)
    resources: list[str] = []
    for path in skill_md.parent.rglob("*"):
        if path.is_file() and path.name != "SKILL.md":
            resources.append(relative_to(path, skill_md.parent))
    return SkillDetail(
        header=header, body=body, phases=phases,
        resources=sorted(resources), full_text=full_text,
    )


def _normalize_allowed_tools(meta: dict[str, Any]) -> list[str]:
    allowed_raw = meta.get("allowed-tools", meta.get("allowed_tools", ""))
    if isinstance(allowed_raw, list):
        return [str(item).strip() for item in allowed_raw if str(item).strip()]
    if isinstance(allowed_raw, str):
        if allowed_raw.startswith("[") and allowed_raw.endswith("]"):
            try:
                loaded = yaml.safe_load(allowed_raw)
                if isinstance(loaded, list):
                    return [str(item).strip() for item in loaded if str(item).strip()]
            except Exception:
                pass
        return [item.strip().strip(",") for item in allowed_raw.split() if item.strip().strip(",")]
    return []


def _render_skill_markdown(meta: dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.lstrip()}"


def _extract_embedded_files(meta: dict[str, Any], files_to_write: dict[str, str]) -> None:
    def _consume(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for name, content in value.items():
                rel = f"{prefix}/{str(name).lstrip('/')}"
                files_to_write.setdefault(rel, str(content))
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for name, content in item.items():
                        rel = f"{prefix}/{str(name).lstrip('/')}"
                        files_to_write.setdefault(rel, str(content))

    for key in ("scripts", "scripts/"):
        if key in meta:
            _consume("scripts", meta.pop(key))
    for key in ("references", "references/"):
        value = meta.get(key)
        if isinstance(value, dict):
            _consume("references", meta.pop(key))
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            sample_keys = {str(k) for item in value for k in item}
            if sample_keys - {"reference", "path", "name"}:
                _consume("references", meta.pop(key))


def _synthesize_skill_body(meta: dict[str, Any], files_to_write: dict[str, str]) -> str:
    body_field = meta.get("body")
    if isinstance(body_field, str) and body_field.strip():
        return body_field.strip()

    lines: list[str] = []
    parameters = meta.get("parameters")
    if isinstance(parameters, list) and parameters:
        lines.extend(["## Parameters", ""])
        for item in parameters:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            type_name = str(item.get("type", "")).strip()
            description = str(item.get("description", "")).strip()
            default = item.get("default")
            text = f"- `{name}`"
            if type_name:
                text += f" ({type_name})"
            if description:
                text += f": {description}"
            if default not in (None, ""):
                text += f" Default: `{default}`."
            lines.append(text)
        lines.append("")

    script_paths = sorted(path for path in files_to_write if path.startswith("scripts/"))
    if script_paths:
        lines.extend(["## Script Usage", ""])
        for path in script_paths:
            script_body = files_to_write.get(path, "")
            if "json.load(sys.stdin)" in script_body or "json.loads(sys.stdin.read(" in script_body:
                lines.append(
                    f"- Call `run_python_script` with `script_path=\"{path}\"` and pass the payload via `stdin_json` when the skill instructs you to use this helper."
                )
            else:
                lines.append(f"- Call `run_python_script` with `script_path=\"{path}\"` when the skill instructs you to use this helper.")
        lines.append("")

    reference_paths = sorted(path for path in files_to_write if path.startswith("references/"))
    if reference_paths:
        lines.extend(["## Reference Usage", ""])
        for path in reference_paths:
            lines.append(f"- Inspect `{path}` with `read_file` before guessing benchmark-specific defaults or edge-case handling.")
        lines.append("")

    if not lines:
        lines.extend(
            [
                "## Execution Guidance",
                "",
                "- Start from the task's actual files and use the allowed tools as the execution boundary.",
                "- Prefer the exact paths and values returned by tools over manually reconstructed guesses.",
            ]
        )
    return "\n".join(lines).strip()


def _normalize_skill_markdown(skill_name: str, skill_md: str, files_to_write: dict[str, str]) -> str:
    normalized = skill_md.replace("\r\n", "\n").strip()
    meta: dict[str, Any] = {}
    body = ""
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end != -1:
            meta = _loose_frontmatter_parse(normalized[4:end])
            body = normalized[end + 5 :].lstrip("\n")
        else:
            meta = _loose_frontmatter_parse(normalized[4:])
    else:
        body = normalized

    if not isinstance(meta, dict):
        meta = {}
    _extract_embedded_files(meta, files_to_write)
    description = str(meta.get("description", "")).strip()
    if description:
        description = " ".join(part.strip() for part in description.splitlines() if part.strip())
    if not description:
        description = f"Task-local skill `{skill_name}`."
    allowed_tools = _normalize_allowed_tools(meta)
    canonical_meta = {
        "name": skill_name,
        "description": description,
    }
    if allowed_tools:
        canonical_meta["allowed-tools"] = allowed_tools
    compatibility = str(meta.get("compatibility", "")).strip()
    if compatibility:
        canonical_meta["compatibility"] = compatibility
    metadata = meta.get("metadata", {})
    if isinstance(metadata, dict) and metadata:
        canonical_meta["metadata"] = metadata
    canonical_body = body.strip() or _synthesize_skill_body(meta, files_to_write)
    return _render_skill_markdown(canonical_meta, canonical_body)


def _augment_skill_markdown(skill_name: str, skill_md: str, files_to_write: dict[str, str]) -> str:
    skill_md = _normalize_skill_markdown(skill_name, skill_md, files_to_write)
    try:
        meta, body = _split_frontmatter(skill_md)
    except Exception:
        return skill_md
    allowed_tools = _normalize_allowed_tools(meta)
    extra_tools: list[str] = []
    if any(path.startswith("references/") for path in files_to_write):
        extra_tools.append("read_file")
    if any(path.startswith("scripts/") for path in files_to_write):
        extra_tools.append("run_python_script")
    for tool_name in extra_tools:
        if tool_name not in allowed_tools:
            allowed_tools.append(tool_name)
    meta["allowed-tools"] = allowed_tools
    return _render_skill_markdown(meta, body)


def write_skill_bundle(skill_library_root: Path, skill_name: str, files_to_write: dict[str, str]) -> Path:
    skill_name = slugify(skill_name)
    skill_dir = skill_library_root / skill_name
    ensure_dir(skill_dir)
    normalized_files = dict(files_to_write)
    if "SKILL.md" in normalized_files:
        normalized_files["SKILL.md"] = _augment_skill_markdown(skill_name, normalized_files["SKILL.md"], normalized_files)
    for relative_path, content in normalized_files.items():
        target = safe_relative_path(skill_dir, relative_path)
        write_text(target, content)
    return skill_dir


def reset_skill_library(skill_library_root: Path) -> None:
    ensure_dir(skill_library_root)
    for child in skill_library_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)


def delete_skill_dirs(skill_library_root: Path, skill_names: list[str]) -> None:
    for name in skill_names:
        skill_dir = safe_relative_path(skill_library_root, slugify(name))
        if skill_dir.exists() and skill_dir.is_dir():
            shutil.rmtree(skill_dir)


def load_experience_buffer(path: Path) -> list[ExperienceEntry]:
    if not path.exists():
        return []
    rows: list[ExperienceEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = yaml.safe_load(line)
        rows.append(ExperienceEntry(**data))
    return rows


def append_experience(path: Path, entry: ExperienceEntry) -> None:
    append_jsonl(path, to_dict(entry))


def reset_experience_buffer(path: Path) -> None:
    ensure_dir(path.parent)
    path.write_text("", encoding="utf-8", newline="\n")


def retrieve_similar_experiences(buffer: list[ExperienceEntry], failure_signature: str, *, limit: int = 3) -> list[ExperienceEntry]:
    if not failure_signature.strip():
        return buffer[:limit]
    target_tokens = {tok for tok in slugify(failure_signature).split("-") if tok}
    scored: list[tuple[int, ExperienceEntry]] = []
    for item in buffer:
        tokens = {tok for tok in slugify(item.failure_signature).split("-") if tok}
        overlap = len(target_tokens & tokens)
        if overlap:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]
