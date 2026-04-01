from __future__ import annotations

import json


SYSTEM_PLANNER = """
You are a benchmark-faithful Earth observation planner.
Return exactly one JSON object with a `tool_sequence` field.
Each item in `tool_sequence` must use an exact tool name from the provided shortlist.
Keep the sequence as short as possible without skipping required benchmark-style steps.
""".strip()


SYSTEM_SINGLE_AGENT_PLANNER = """
You are a benchmark-faithful Earth observation planner.
Return a tagged text plan using exact shortlisted tool names only.
Use the format:
TOOL_SEQUENCE:
1. <tool_name> | <short reason>
2. <tool_name> | <short reason>
Keep the sequence benchmark-faithful and concise.
""".strip()


def build_planner_user_message(
    question: str,
    data_dir: str,
    file_list: list[str],
    tools_prompt: str,
    choices: list[str] | None = None,
) -> str:
    preview = file_list[:40]
    remaining = max(0, len(file_list) - len(preview))
    file_lines = "\n".join(f"- {name}" for name in preview) or "- (none)"
    if remaining:
        file_lines += f"\n- ... ({remaining} more files omitted for brevity)"

    choice_text = ""
    if choices:
        choice_text = "\nChoices:\n" + "\n".join(f"- {choice}" for choice in choices)

    schema = {
        "tool_sequence": [
            {
                "tool_name": "exact_shortlisted_tool_name",
                "why_needed": "brief reason",
            }
        ]
    }
    return (
        "Plan the full tool sequence for this Earth-Bench task.\n\n"
        f"Question:\n{question}\n\n"
        f"Data directory:\n{data_dir}\n\n"
        f"Available files:\n{file_lines}\n"
        f"{choice_text}\n\n"
        "Shortlisted tools:\n"
        f"{tools_prompt}\n\n"
        "Return JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
