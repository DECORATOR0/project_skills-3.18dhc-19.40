from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass
class PlannerSequenceStep:
    tool_name: str
    why_needed: str = ""


def _extract_tool_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("tool_name", "name", "tool", "canonical_name"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return ""


def _extract_reason(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("why_needed", "rationale", "reason", "description", "why"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return ""


def _parse_tagged_single_agent_response(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"tool_sequence": []}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    steps: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^[\-\*\d\.\)\s]+", "", stripped)
        if stripped.upper().startswith("TOOL_SEQUENCE"):
            continue
        parts = [part.strip() for part in stripped.split("|", maxsplit=1)]
        tool_name = parts[0].strip("`")
        if not tool_name:
            continue
        reason = parts[1] if len(parts) > 1 else stripped
        steps.append({"tool_name": tool_name, "why_needed": reason})
    return {"tool_sequence": steps}


def _sanitize_tool_sequence(tool_sequence: list[Any], item: Any, shortlisted_tools: list[Any]) -> list[PlannerSequenceStep]:
    valid_names = {tool.canonical_name for tool in shortlisted_tools}
    sanitized: list[PlannerSequenceStep] = []
    for raw_step in tool_sequence or []:
        tool_name = _extract_tool_name(raw_step)
        if tool_name not in valid_names:
            continue
        sanitized.append(
            PlannerSequenceStep(
                tool_name=tool_name,
                why_needed=_extract_reason(raw_step) or "Selected from the shortlist for this question.",
            )
        )
    return sanitized


def _repair_single_sequence(
    sanitized: list[PlannerSequenceStep],
    item: Any,
    shortlisted_tools: list[Any],
    profile: dict[str, Any],
) -> list[PlannerSequenceStep]:
    if not sanitized:
        return []

    valid_names = {tool.canonical_name for tool in shortlisted_tools}
    repaired: list[PlannerSequenceStep] = []
    if "get_filelist" in valid_names and sanitized[0].tool_name != "get_filelist":
        repaired.append(
            PlannerSequenceStep(
                tool_name="get_filelist",
                why_needed="Inspect available files before later benchmark steps.",
            )
        )

    for step in sanitized:
        if step.tool_name not in valid_names:
            continue
        repaired.append(
            PlannerSequenceStep(
                tool_name=step.tool_name,
                why_needed=step.why_needed or "Selected from the shortlist for this question.",
            )
        )
    return repaired
