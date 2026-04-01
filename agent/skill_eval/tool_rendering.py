"""
Render planner-facing tool metadata in a LangChain-like text format.

The goal is to expose tool names, descriptions, and args in plain text without
adding a hard dependency on LangChain.
"""
from __future__ import annotations

import json
from typing import Iterable

from .tool_catalog import ToolMeta


def tool_args_schema(tool: ToolMeta) -> dict:
    properties: dict[str, dict] = {}
    required: list[str] = []
    for param in tool.parameters:
        name = param["name"]
        schema = {"type": param.get("type", "any")}
        if "default" in param:
            schema["default"] = param["default"]
        else:
            required.append(name)
        properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def render_text_description_and_args(tools: Iterable[ToolMeta]) -> str:
    """
    Mimic the format of LangChain's render_text_description_and_args.

    Example line:
    get_filelist: List files in a folder, args: {"dir_path": {"type": "str"}}
    """
    lines: list[str] = []
    for tool in tools:
        schema = tool_args_schema(tool)
        lines.append(
            f"{tool.canonical_name}: {tool.short_description(220)}, "
            f"args: {json.dumps(schema['properties'], ensure_ascii=False, sort_keys=True)}"
        )
    return "\n".join(lines)


def render_detailed_tool_block(tools: Iterable[ToolMeta]) -> str:
    blocks: list[str] = []
    for tool in tools:
        schema = tool_args_schema(tool)
        blocks.append(
            "\n".join(
                [
                    f"- Tool: {tool.canonical_name}",
                    f"  Toolkit: {tool.toolkit}",
                    f"  Stage: {tool.stage_hint}",
                    f"  Tags: {', '.join(tool.task_tags)}",
                    f"  Description: {tool.short_description(220)}",
                    f"  Args JSON: {json.dumps(schema, ensure_ascii=False, sort_keys=True)}",
                ]
            )
        )
    return "\n\n".join(blocks)
