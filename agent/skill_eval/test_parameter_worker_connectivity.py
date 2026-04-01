"""
Connectivity checks for the local skill parameter model endpoint.

This script runs two tests:
1. Plain chat completion against the configured parameter model.
2. A real worker-style call using the same parameter-worker path as Stage 2.
"""
from __future__ import annotations

import json

from openai import OpenAI

from .benchmark_loader import load_benchmark
from .config import (
    PARAMETER_MODEL_API_KEY,
    PARAMETER_MODEL_BASE_URL,
    PARAMETER_MODEL_NAME,
)
from .parameter_worker import choose_tool_arguments
from .prompt_builder import build_worker_user_prompt
from .skill_router import route_skill, tools_for_skill
from .tool_catalog import build_catalog
from .tool_rendering import render_text_description_and_args


def plain_chat_test() -> dict:
    client = OpenAI(base_url=PARAMETER_MODEL_BASE_URL, api_key=PARAMETER_MODEL_API_KEY)
    resp = client.chat.completions.create(
        model=PARAMETER_MODEL_NAME,
        messages=[{"role": "user", "content": "Reply with exactly ok"}],
        max_tokens=16,
        temperature=0,
    )
    return {"content": resp.choices[0].message.content}


def worker_style_test(question_id: str = "1") -> dict:
    item = load_benchmark(question_ids=[question_id])[0]
    catalog = build_catalog()
    skill = route_skill(item.question_id, item.question_text, item.file_list)
    skill_tools = tools_for_skill(skill, catalog)
    prompt = build_worker_user_prompt(
        question=item.question_text,
        data_path=item.data_dir,
        rendered_tools=render_text_description_and_args(skill_tools),
        context=None,
    )

    decision = choose_tool_arguments(prompt, planned_tool_name="get_filelist", model=PARAMETER_MODEL_NAME)
    return {
        "tool_name": decision.tool_name,
        "arguments": decision.arguments,
        "rationale": decision.rationale,
        "raw_response": decision.raw_response,
        "cleaned_response": decision.cleaned_response,
        "model": decision.model,
    }


def main() -> None:
    results: dict[str, dict] = {}
    for name, fn in [
        ("plain_chat_test", plain_chat_test),
        ("worker_style_test", worker_style_test),
    ]:
        try:
            results[name] = {"ok": True, "result": fn()}
        except Exception as exc:  # pragma: no cover - diagnostic script
            results[name] = {"ok": False, "error": str(exc)}

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
