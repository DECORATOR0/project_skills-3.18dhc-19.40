from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .config import SystemConfig
from .schemas import SkillHeader
from .tools import ToolContext, Toolbox, ToolSpec


def _variant_names(name: str) -> list[str]:
    names = [name]
    if name.startswith("calculate_batch_"):
        names.append("calculate_" + name[len("calculate_batch_"):])
    elif name.startswith("calculate_"):
        names.append("calculate_batch_" + name[len("calculate_"):])
    return names


def _compact_spec(spec: ToolSpec) -> dict[str, object]:
    description = " ".join(spec.description.split())
    if len(description) > 280:
        description = description[:277] + "..."
    return {
        "name": spec.name,
        "description": description,
        "parameters": spec.parameters,
        "source": spec.source,
    }


@lru_cache(maxsize=8)
def _load_tool_specs(
    workspace_root: str,
    skill_library_root: str,
    run_root: str,
    python_executable: str,
    shell_program: str,
) -> tuple[ToolSpec, ...]:
    context = ToolContext(
        workspace_root=Path(workspace_root),
        skill_library_root=Path(skill_library_root),
        temp_root=Path(run_root) / "_tool_hint_runtime",
        python_executable=python_executable,
        shell_program=shell_program,
    )
    return tuple(Toolbox(context).specs())


def render_relevant_tools_json(
    config: SystemConfig,
    *,
    task_prompt: str,
    gold_tool_names: list[str],
    skill_headers: list[SkillHeader],
) -> str:
    specs = _load_tool_specs(
        str(config.workspace_root),
        str(config.skill_library_root),
        str(config.run_root),
        config.runtime.python_executable,
        config.runtime.shell_program,
    )
    spec_map = {spec.name: spec for spec in specs}
    ordered_names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name in seen or name not in spec_map:
            return
        seen.add(name)
        ordered_names.append(name)

    seed_names: set[str] = set(gold_tool_names)
    seed_names.update({"read_file", "run_python_script"})
    for header in skill_headers:
        seed_names.update(header.allowed_tools or [])

    keywords = {
        token
        for token in re.findall(r"[a-z0-9_]+", task_prompt.lower())
        if len(token) >= 4
    }

    for name in sorted(seed_names):
        for variant in _variant_names(name):
            add(variant)

    for spec in specs:
        lowered = spec.name.lower()
        if any(keyword in lowered for keyword in keywords):
            add(spec.name)

    payload = [_compact_spec(spec_map[name]) for name in ordered_names]
    return json.dumps(payload, ensure_ascii=False, indent=2)
