"""
Prompt builders for skill-routed Earth-Bench evaluation.
"""
from __future__ import annotations

from textwrap import dedent

from .config import ANSWER_SELECTION_NOTE, WORKER_SYSTEM_PROMPT


def build_worker_user_prompt(
    *,
    question: str,
    data_path: str,
    rendered_tools: str,
    context: str | None = None,
) -> str:
    parts: list[str] = []
    if context:
        parts.append("Context:\n" + context.strip())
    else:
        parts.append("Question:\n" + question.strip())
    parts.append(f"Relevant datas are stored at {data_path}")
    parts.append("Available tools and argument schemas:\n" + rendered_tools.strip())
    parts.append(
        dedent(
            """
            Return exactly one next tool call decision.
            Focus on the single most useful next action given the current state.
            If the next tool parameters depend on earlier tool outputs, use the current context and prior outputs instead of guessing unseen paths or values.
            
            Prefer using the provided tool-calling interface directly if available.
            If tool calling is unavailable, respond in this easy-to-parse format:
            TOOL_NAME: <tool name>
            ARGUMENTS_JSON:
            {"arg_name": "arg_value"}
            RATIONALE: <short rationale>
            """
        ).strip()
    )
    return "\n\n".join(parts)


def build_initial_context(question: str) -> str:
    return f"Original question:\n{question.strip()}"


def build_step_context(
    *,
    question: str,
    prior_steps: list[str],
    latest_observation: str | None,
) -> str:
    parts = [f"Original question:\n{question.strip()}"]
    if prior_steps:
        parts.append("Executed steps so far:\n" + "\n".join(f"- {s}" for s in prior_steps))
    if latest_observation:
        parts.append("Latest observation:\n" + latest_observation.strip())
    return "\n\n".join(parts)


def build_planned_tool_context(
    *,
    question: str,
    planned_tool_name: str,
    prior_steps: list[str],
    latest_observation: str | None,
) -> str:
    parts = [
        f"Original question:\n{question.strip()}",
        f"Planned next tool:\n{planned_tool_name}",
    ]
    if prior_steps:
        parts.append("Executed steps so far:\n" + "\n".join(f"- {s}" for s in prior_steps))
    if latest_observation:
        parts.append("Latest observation:\n" + latest_observation.strip())
    parts.append(
        "Return arguments for the planned next tool. Keep the action aligned with the planned tool."
    )
    return "\n\n".join(parts)


def build_choice_selector_messages(
    *,
    question: str,
    choices: list[str],
    execution_summary: str,
) -> list[dict]:
    choice_text = "\n".join(f"{idx + 1}. {choice}" for idx, choice in enumerate(choices))
    system = dedent(
        """
        You are the final answer selector for Earth-Bench.
        You are given a benchmark question, four candidate answers, and a concise summary of the executed skill/tool trace.
        Select the single best answer choice. Do not invent a new answer.
        Return JSON with:
        {
          "choice_index": 1-based integer,
          "choice_text": "exact option text",
          "reason": "short justification"
        }
        """
    ).strip()
    user = "\n\n".join(
        [
            f"Question:\n{question.strip()}",
            f"Choices:\n{choice_text}",
            f"Execution summary:\n{execution_summary.strip()}",
            ANSWER_SELECTION_NOTE,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def worker_system_prompt() -> str:
    return WORKER_SYSTEM_PROMPT
