from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from .schemas import ExperienceEntry, SkillDetail, SkillHeader, to_dict
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


def load_skill_detail(header: SkillHeader) -> SkillDetail:
    skill_md = Path(header.skill_md_path)
    full_text = read_text(skill_md)
    _, body = _split_frontmatter(full_text)
    resources: list[str] = []
    for path in skill_md.parent.rglob("*"):
        if path.is_file() and path.name != "SKILL.md":
            resources.append(relative_to(path, skill_md.parent))
    return SkillDetail(header=header, body=body, resources=sorted(resources), full_text=full_text)


def _normalize_allowed_tools(meta: dict[str, Any]) -> list[str]:
    allowed_raw = meta.get("allowed-tools", "")
    if isinstance(allowed_raw, list):
        return [str(item).strip() for item in allowed_raw if str(item).strip()]
    if isinstance(allowed_raw, str):
        return [item for item in allowed_raw.split() if item]
    return []


def _render_skill_markdown(meta: dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.lstrip()}"


def _augment_skill_markdown(skill_md: str, files_to_write: dict[str, str]) -> str:
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
        normalized_files["SKILL.md"] = _augment_skill_markdown(normalized_files["SKILL.md"], normalized_files)
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
