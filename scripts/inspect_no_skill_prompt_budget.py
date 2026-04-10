from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.skill_eval.config import TOOL_FILES, TOOL_SOURCE_DIR
from agent.skill_eval.tool_catalog import ToolMeta, build_catalog, parse_tool_file
from nlrl_skills.agent_loop import _count_prompt_tokens, _load_tokenizer
from nlrl_skills.config import SystemConfig, load_system_config
from nlrl_skills.data import load_converted_dataset, select_task
from nlrl_skills.prompting import load_prompt, render_prompt
from nlrl_skills.schemas import DatasetTask, LLMMessage
from nlrl_skills.tools import ToolContext, Toolbox

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "system.eval.local_qwen3_8b_stream140.json"


@dataclass(frozen=True)
class PromptSurface:
    key: str
    label: str
    describe_tools: Callable[[SystemConfig], tuple[list[str], str]]


def _task_payload_json(task: DatasetTask) -> str:
    preview_limit = 20
    file_list_preview = task.file_list[:preview_limit]
    return json.dumps(
        {
            "task_id": task.task_id,
            "prompt": task.prompt,
            "choices": task.choices,
            "data_dir": task.data_dir,
            "file_count": len(task.file_list),
            "file_list_preview": file_list_preview,
            "file_list_preview_count": len(file_list_preview),
            "file_list_preview_is_partial": len(task.file_list) > len(file_list_preview),
            "gold_answer_hidden": True,
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_context(config: SystemConfig) -> ToolContext:
    return ToolContext(
        workspace_root=config.workspace_root,
        skill_library_root=config.skill_library_root,
        temp_root=config.run_root / "temp",
        python_executable=config.runtime.python_executable,
        shell_program=config.runtime.shell_program,
    )


def _nlrl_visible_tools(config: SystemConfig) -> tuple[list[str], str]:
    toolbox = Toolbox(_tool_context(config))
    tool_names = [spec.name for spec in toolbox.specs()]
    return tool_names, toolbox.tool_prompt(None)


def _tool_meta_jsonl(tools: list[ToolMeta]) -> str:
    return "\n".join(
        json.dumps(
            {
                "name": tool.canonical_name,
                "description": tool.short_description(220),
                "parameters": tool.parameters,
                "source": tool.toolkit,
            },
            ensure_ascii=False,
        )
        for tool in tools
    )


def _actual_mcp_tools(_: SystemConfig) -> tuple[list[str], str]:
    tools: list[ToolMeta] = []
    for module_file in TOOL_FILES:
        tools.extend(parse_tool_file(TOOL_SOURCE_DIR / module_file))
    return [tool.canonical_name for tool in tools], _tool_meta_jsonl(tools)


def _catalog_with_synthetic(_: SystemConfig) -> tuple[list[str], str]:
    tools = build_catalog()
    return [tool.canonical_name for tool in tools], _tool_meta_jsonl(tools)


SURFACES: dict[str, PromptSurface] = {
    "nlrl-visible": PromptSurface(
        key="nlrl-visible",
        label="Current nlrl_skills runtime-visible tools",
        describe_tools=_nlrl_visible_tools,
    ),
    "actual-mcp": PromptSurface(
        key="actual-mcp",
        label="Actual MCP-decorated tools from agent/tools",
        describe_tools=_actual_mcp_tools,
    ),
    "catalog": PromptSurface(
        key="catalog",
        label="skill_eval catalog including synthetic helpers",
        describe_tools=_catalog_with_synthetic,
    ),
}


def _measure_surface(
    config: SystemConfig,
    task: DatasetTask,
    *,
    surface: PromptSurface,
    requested_input_budget: int,
    requested_output_max_tokens: int,
) -> dict[str, object]:
    tool_names, tools_json = surface.describe_tools(config)
    base_system = render_prompt(
        config.prompt_root / "executor_system_no_skill.md",
        max_steps=config.runtime.max_executor_steps,
    )
    user_prompt = render_prompt(
        config.prompt_root / "executor_user_no_skill.md",
        task_json=_task_payload_json(task),
    )
    tool_protocol = load_prompt(config.prompt_root / "tool_agent_protocol.md")
    system_message = base_system + "\n\n" + tool_protocol.format(tools_json=tools_json)
    messages = [
        LLMMessage(role="system", content=system_message),
        LLMMessage(role="user", content=user_prompt),
    ]

    tokenizer = _load_tokenizer(config.runtime.executor_tokenizer_path)

    def text_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    step1_prompt_tokens = _count_prompt_tokens(
        messages,
        tokenizer_path=config.runtime.executor_tokenizer_path,
        enable_thinking=config.executor.enable_thinking,
    )
    return {
        "surface_key": surface.key,
        "surface_label": surface.label,
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "tools_json_tokens": text_tokens(tools_json),
        "tools_json_chars": len(tools_json),
        "system_message_tokens": text_tokens(system_message),
        "system_message_chars": len(system_message),
        "user_prompt_tokens": text_tokens(user_prompt),
        "user_prompt_chars": len(user_prompt),
        "step1_prompt_tokens": step1_prompt_tokens,
        "requested_input_budget": requested_input_budget,
        "requested_output_max_tokens": requested_output_max_tokens,
        "requested_input_budget_remaining": requested_input_budget - step1_prompt_tokens,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the initial no-skill executor prompt budget under different tool-surface definitions."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the nlrl system config JSON.",
    )
    parser.add_argument(
        "--task-id",
        help="Task id or original question id. Defaults to the first converted dataset task.",
    )
    parser.add_argument(
        "--surface",
        choices=["all", *SURFACES.keys()],
        default="all",
        help="Which tool surface to inspect.",
    )
    parser.add_argument(
        "--input-budget",
        type=int,
        default=32768,
        help="Target input-token budget for reporting remaining headroom.",
    )
    parser.add_argument(
        "--output-max-tokens",
        type=int,
        default=8192,
        help="Target output cap for the planned baseline run. This does not change prompt token counts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_system_config(args.config)
    tasks = load_converted_dataset(config.converted_dataset_path)
    task = select_task(tasks, args.task_id)
    selected_surfaces = (
        list(SURFACES.values())
        if args.surface == "all"
        else [SURFACES[args.surface]]
    )
    payload = {
        "config_path": str(Path(args.config).resolve()),
        "model": config.executor.model,
        "tokenizer_path": config.runtime.executor_tokenizer_path,
        "task_id": task.task_id,
        "task_prompt_head": task.prompt[:200].replace("\n", " "),
        "task_file_count": len(task.file_list),
        "config_executor_max_tokens": config.executor.max_tokens,
        "config_runtime_total_token_budget": config.runtime.executor_total_token_budget,
        "requested_input_budget": args.input_budget,
        "requested_output_max_tokens": args.output_max_tokens,
        "surfaces": [
            _measure_surface(
                config,
                task,
                surface=surface,
                requested_input_budget=args.input_budget,
                requested_output_max_tokens=args.output_max_tokens,
            )
            for surface in selected_surfaces
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
