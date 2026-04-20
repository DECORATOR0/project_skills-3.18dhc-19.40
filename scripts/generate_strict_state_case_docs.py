#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ACTION_PATTERN = re.compile(
    r"(?:(?:<THOUGHT>(?P<thought_text>.*?)</THOUGHT>)\s*)?"
    r"(?:(?P<call><CALL>(?P<call_name>.*?)</CALL>\s*<ARGS>(?P<call_args>.*?)</ARGS>)"
    r"|(?P<next><NEXT>(?P<next_name>.*?)</NEXT>)"
    r"|(?P<answer><ANSWER>(?P<answer_text>.*?)</ANSWER>)"
    r"|(?P<feedback>\[runtime_feedback\]\s*(?P<feedback_text>.*?))(?=(?:\s*<THOUGHT>|\s*<CALL>|\s*<NEXT>|\s*<ANSWER>|\s*\[runtime_feedback\]|\Z)))",
    re.S,
)

ERROR_LIKE_MARKERS = (
    "failed to call model",
    "invalid path",
    "failed to open",
    "not found",
    "does not exist",
    "no such file",
    "exception",
    "traceback",
    "unknown mode",
    "missing required positional argument",
    "missing required positional arguments",
    "jsondecodeerror",
    "valueerror",
    "keyerror",
    "typeerror",
)

GUESS_MARKERS = (
    "best estimate",
    "closest",
    "likely",
    "typical",
    "assumption",
    "mid-range",
    "plausible",
    "plausibility",
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return load_json(path)


def now_stamp() -> str:
    return datetime.now().strftime("%y.%-m.%-d_%H%M")


def clean_text(text: object, limit: int = 160) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "error" in parsed:
            text = str(parsed["error"])
        else:
            text = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def try_parse_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def flatten_payload_texts(value: Any, *, depth: int = 0) -> list[str]:
    if value is None or depth > 3:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        texts = [text]
        parsed = try_parse_json(text)
        if parsed is not None and parsed != text:
            texts.extend(flatten_payload_texts(parsed, depth=depth + 1))
        return texts
    if isinstance(value, dict):
        texts: list[str] = []
        for key, item in value.items():
            texts.append(str(key))
            texts.extend(flatten_payload_texts(item, depth=depth + 1))
        return texts
    if isinstance(value, list):
        texts: list[str] = []
        for item in value[:8]:
            texts.extend(flatten_payload_texts(item, depth=depth + 1))
        return texts
    return [str(value)]


def summarize_payload(value: Any, *, limit: int = 320) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        parsed = try_parse_json(value)
        if parsed is not None:
            return clean_text(parsed, limit=limit)
        return clean_text(value, limit=limit)
    return clean_text(value, limit=limit)


def summarize_observation(observation: object) -> str:
    return clean_text(observation, limit=180)


def render_choices(task_payload: dict) -> list[str]:
    choices = task_payload.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return ["- `(no choices found)`"]
    lines: list[str] = []
    for idx, choice in enumerate(choices):
        label = chr(ord("A") + idx) if idx < 26 else f"Option{idx + 1}"
        lines.append(f"- `{label}`: {choice}")
    return lines


def observation_is_error_like(observation: object) -> bool:
    return bool(first_error_signal({"observation": observation})[0])


def first_error_signal(payload: Any) -> tuple[str, str]:
    if isinstance(payload, dict):
        error_value = payload.get("error")
        if error_value:
            return "error-field", clean_text(error_value, limit=220)

        returncode = payload.get("returncode")
        if returncode not in (None, 0):
            detail = payload.get("stderr") or payload.get("stdout") or f"returncode={returncode}"
            return "returncode-nonzero", clean_text(detail, limit=220)

        for stream_name in ("stderr", "stdout", "observation", "raw_result"):
            stream_value = payload.get(stream_name)
            if not stream_value:
                continue
            for fragment in flatten_payload_texts(stream_value):
                lower = fragment.lower()
                if any(marker in lower for marker in ERROR_LIKE_MARKERS):
                    return f"{stream_name}-error", clean_text(fragment, limit=220)

    for fragment in flatten_payload_texts(payload):
        lower = fragment.lower()
        if any(marker in lower for marker in ERROR_LIKE_MARKERS):
            return "error-like-text", clean_text(fragment, limit=220)

    return "", ""


def analyze_tool_record(record: dict[str, Any]) -> dict[str, str | bool]:
    signal, detail = first_error_signal(
        {
            "error": record.get("error"),
            "returncode": record.get("raw_result", {}).get("returncode") if isinstance(record.get("raw_result"), dict) else None,
            "stderr": record.get("raw_result", {}).get("stderr") if isinstance(record.get("raw_result"), dict) else None,
            "stdout": record.get("raw_result", {}).get("stdout") if isinstance(record.get("raw_result"), dict) else None,
            "observation": record.get("observation"),
            "raw_result": record.get("raw_result"),
        }
    )
    success_flag = bool(record.get("success", False))
    if not success_flag:
        status = "failed"
    elif signal:
        status = "degraded"
    else:
        status = "success"
    return {
        "status": status,
        "signal": signal or "none",
        "detail": detail,
        "success_flag": success_flag,
    }


def extract_success_snippet(observation: object) -> str:
    text = clean_text(observation, limit=180)
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        return ""
    if len(text) > 160:
        return ""
    if any(ch.isdigit() for ch in text):
        return text
    return ""


def phase_path(transitions: list[dict[str, object]]) -> str:
    phases = ["INIT"]
    phases.extend(str(item.get("to_phase", "")) for item in transitions if item.get("to_phase"))
    return " -> ".join(phases)


def final_phase(transitions: list[dict[str, object]]) -> str:
    if not transitions:
        return "INIT"
    return str(transitions[-1].get("to_phase") or "INIT")


def parse_call_args(call_args: str) -> Any:
    parsed = try_parse_json(call_args)
    if parsed is not None:
        return parsed
    cleaned = call_args.strip()
    if not cleaned:
        return {}
    return {"_raw": cleaned}


def parse_actions(
    raw_output: str,
    tools: list[dict[str, object]],
    transitions: list[dict[str, object]],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    tool_idx = 0
    transition_idx = 0

    for match in ACTION_PATTERN.finditer(raw_output or ""):
        if match.group("call"):
            tool = tools[tool_idx] if tool_idx < len(tools) else {}
            tool_idx += 1
            analysis = analyze_tool_record(tool)
            actions.append(
                {
                    "kind": "call",
                    "tool_name": match.group("call_name").strip(),
                    "step_index": tool.get("step_index"),
                    "status": analysis["status"],
                    "signal": analysis["signal"],
                    "signal_detail": analysis["detail"],
                    "success_flag": analysis["success_flag"],
                    "thought": clean_text(tool.get("thought") or match.group("thought_text") or "", limit=420),
                    "arguments": tool.get("arguments") or parse_call_args(match.group("call_args") or ""),
                    "observation": tool.get("observation", ""),
                    "raw_result": tool.get("raw_result"),
                    "error": tool.get("error", ""),
                    "feedbacks": [],
                }
            )
            continue

        if match.group("next"):
            target = match.group("next_name").strip()
            valid = False
            transition = {}
            if transition_idx < len(transitions) and target == str(transitions[transition_idx].get("to_phase", "")).strip():
                valid = True
                transition = transitions[transition_idx]
                transition_idx += 1
            actions.append(
                {
                    "kind": "next",
                    "target": target,
                    "valid": valid,
                    "step_index": transition.get("step_index") if valid else None,
                    "from_phase": transition.get("from_phase") if valid else None,
                    "thought": clean_text(
                        transition.get("thought") if valid else (match.group("thought_text") or ""),
                        limit=420,
                    ),
                    "feedbacks": [],
                }
            )
            continue

        if match.group("answer"):
            actions.append(
                {
                    "kind": "answer",
                    "answer": clean_text(match.group("answer_text"), limit=80),
                    "thought": clean_text(match.group("thought_text") or "", limit=420),
                    "step_index": None,
                    "valid": True,
                    "feedbacks": [],
                }
            )
            continue

        feedback = clean_text(match.group("feedback_text"), limit=200)
        if actions:
            actions[-1].setdefault("feedbacks", []).append(feedback)
        else:
            actions.append({"kind": "feedback", "text": feedback})

    for action in actions:
        feedback_blob = " ".join(action.get("feedbacks", []))
        if action["kind"] == "next" and ("Invalid phase transition." in feedback_blob or "Unknown phase." in feedback_blob):
            action["valid"] = False
        if action["kind"] == "answer" and "Invalid answer output." in feedback_blob:
            action["valid"] = False

    return actions


def describe_tool_failures(tools: list[dict[str, object]]) -> list[str]:
    messages: list[str] = []
    failed_counter: Counter[str] = Counter()
    degraded_counter: Counter[str] = Counter()

    for item in tools:
        tool_name = str(item.get("tool_name") or "")
        observation = item.get("observation", "")
        if not item.get("success", False):
            failed_counter[tool_name] += 1
        elif observation_is_error_like(observation):
            degraded_counter[tool_name] += 1

    if failed_counter:
        joined = "，".join(f"`{name}` x{count}" for name, count in failed_counter.items())
        messages.append(f"关键工具失败：{joined}。")
    if degraded_counter:
        joined = "，".join(f"`{name}` x{count}" for name, count in degraded_counter.items())
        messages.append(f"有些工具表面返回成功，但内容带错误信号：{joined}。")
    return messages


def compact_reason_lines(reason_lines: list[str], limit: int = 2) -> list[str]:
    out: list[str] = []
    for item in reason_lines:
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def render_iteration_compare_section(previous_summary: dict[str, object] | None, current_summary: dict[str, object]) -> list[str]:
    if not previous_summary:
        return []

    prev_success = bool(previous_summary.get("success"))
    cur_success = bool(current_summary.get("success"))
    if prev_success and cur_success:
        return []

    prev_label = "对" if prev_success else "错"
    cur_label = "对" if cur_success else "错"
    prev_reasons = compact_reason_lines(list(previous_summary.get("reason_lines") or []))
    cur_reasons = compact_reason_lines(list(current_summary.get("reason_lines") or []))

    lines = [
        "## 与上一轮对比",
        "",
        f"- 状态变化：`{prev_label} -> {cur_label}`",
    ]

    if prev_success:
        if prev_reasons:
            lines.append(f"- 上一轮为什么对：{'；'.join(prev_reasons)}")
        else:
            lines.append("- 上一轮为什么对：上一轮形成了可用证据链并命中了金标。")
    else:
        if prev_reasons:
            lines.append(f"- 上一轮为什么错：{'；'.join(prev_reasons)}")
        else:
            lines.append("- 上一轮为什么错：上一轮没有形成可靠证据闭环。")

    if cur_success:
        if cur_reasons:
            lines.append(f"- 这一轮为什么对：{'；'.join(cur_reasons)}")
        else:
            lines.append("- 这一轮为什么对：这一轮形成了可用证据链并命中了金标。")
    else:
        if cur_reasons:
            lines.append(f"- 这一轮为什么错：{'；'.join(cur_reasons)}")
        else:
            lines.append("- 这一轮为什么错：这一轮没有形成可靠证据闭环。")

    lines.append("")
    return lines


def build_reason_lines(
    *,
    task_success: bool,
    transitions: list[dict[str, object]],
    tools: list[dict[str, object]],
    raw_output: str,
    final_answer: str,
    executor_summary: str,
) -> list[str]:
    reasons: list[str] = []
    invalid_phase_count = raw_output.count("Invalid phase transition.")
    unknown_phase_count = raw_output.count("Unknown phase.")
    invalid_answer_count = raw_output.count("Invalid answer output.")
    fallback_conclude = any("[fallback conclude]" in str(item.get("thought", "")) for item in transitions)
    resolved_conclude = any("[resolved conclude]" in str(item.get("thought", "")) for item in transitions)
    lower_summary = (executor_summary or "").lower()
    guessy = any(marker in lower_summary for marker in GUESS_MARKERS)
    degraded = any(observation_is_error_like(item.get("observation", "")) for item in tools)
    failed = any(not item.get("success", False) for item in tools)

    if invalid_phase_count:
        reasons.append(f"中途有 `{invalid_phase_count}` 次非法 phase 跳转，被严格状态机拦下后才纠正。")
    if unknown_phase_count:
        reasons.append(f"中途有 `{unknown_phase_count}` 次未知 phase 输出。")
    if invalid_answer_count:
        reasons.append(f"中途有 `{invalid_answer_count}` 次非 `CONCLUDE` 提前作答，被运行时约束拦下。")

    reasons.extend(describe_tool_failures(tools))

    if task_success:
        if not failed and not degraded and not invalid_phase_count and not invalid_answer_count:
            reasons.append("phase 路径和关键工具链基本闭合，答案是按正常流程得出的。")
        else:
            reasons.append("结果命中了金标，但轨迹不算稳，存在纠偏、失败重试或弱证据收尾。")
        if fallback_conclude:
            reasons.append("最后是 fallback `CONCLUDE` 收尾，说明 step 预算已经逼近上限。")
        elif resolved_conclude:
            reasons.append("最后进入 `CONCLUDE` 收尾，收尾动作是明确记录过的。")
        elif final_answer:
            reasons.append("没有显式进入 `CONCLUDE`，是在工作 phase 里直接给出答案。")
        if guessy:
            reasons.append("收尾措辞带估计色彩，这类成功更像脆弱成功。")
    else:
        if failed or degraded:
            reasons.append("核心问题还是工具链或结果链没有真正闭合。")
        if fallback_conclude:
            reasons.append("最后靠 fallback `CONCLUDE` 兜底收尾，但证据不足以支撑正确答案。")
        elif resolved_conclude:
            reasons.append("虽然进入了 `CONCLUDE`，但进入时证据已经残缺，最终答案只是收尾。")
        else:
            reasons.append("没有形成可靠的 `CONCLUDE` 证据闭环。")
        if guessy:
            reasons.append("收尾文本里有明显估计语气，答案更接近猜测。")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in reasons:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def render_action_overview(action: dict[str, object], final_phase_name: str) -> str:
    if action["kind"] == "call":
        prefix = f"- step {action.get('step_index')}: 调用 `{action['tool_name']}`"
        status = str(action.get("status"))
        if status == "failed":
            suffix = "，失败"
        elif status == "degraded":
            suffix = "，返回了错误样式结果"
        else:
            suffix = "，成功"
        detail = ""
        if status in {"failed", "degraded"}:
            signal = str(action.get("signal") or "none")
            detail = f"。信号：`{signal}`。返回：`{summarize_observation(action.get('observation', ''))}`"
        else:
            snippet = extract_success_snippet(action.get("observation", ""))
            if snippet:
                detail = f"。结果摘要：`{snippet}`"
        return prefix + suffix + detail

    if action["kind"] == "next":
        if action.get("valid"):
            thought = clean_text(str(action.get("thought", "")).replace("[resolved conclude]", "").replace("[fallback conclude]", ""), limit=120)
            detail = f"。原因：{thought}" if thought else ""
            return f"- step {action.get('step_index')}: 切换到 `{action['target']}`，合法{detail}"
        feedback = " ".join(str(x) for x in action.get("feedbacks", []))
        return f"- action: 试图切换到 `{action['target']}`，被拦截。反馈：`{clean_text(feedback, limit=160)}`"

    if action["kind"] == "answer":
        answer = action.get("answer") or "(empty)"
        if action.get("valid"):
            return f"- final: 在 `{final_phase_name}` 输出答案 `{answer}`"
        feedback = " ".join(str(x) for x in action.get("feedbacks", []))
        return f"- action: 试图提前输出答案 `{answer}`，被拦截。反馈：`{clean_text(feedback, limit=160)}`"

    return f"- action: `{clean_text(action.get('text', ''), limit=160)}`"


def render_action_detail(action: dict[str, object], final_phase_name: str) -> list[str]:
    lines: list[str] = []
    kind = str(action.get("kind"))

    if kind == "call":
        step_index = action.get("step_index")
        tool_name = action.get("tool_name")
        lines.extend(
            [
                f"### step {step_index} `{tool_name}`",
                "",
                f"- 动作：调用 `{tool_name}`",
                f"- 运行时状态：`{action.get('status')}`",
                f"- 原始 success 标记：`{action.get('success_flag')}`",
            ]
        )
        signal = str(action.get("signal") or "none")
        if signal != "none":
            lines.append(f"- 错误信号：`{signal}`")
        signal_detail = summarize_payload(action.get("signal_detail"), limit=260)
        if signal_detail:
            lines.append(f"- 错误细节：`{signal_detail}`")
        thought = str(action.get("thought") or "").strip()
        if thought:
            lines.append(f"- executor thought：{thought}")
        args_text = summarize_payload(action.get("arguments"), limit=420)
        if args_text:
            lines.append(f"- 调用参数：`{args_text}`")
        observation_text = summarize_payload(action.get("observation"), limit=420)
        if observation_text:
            lines.append(f"- observation：`{observation_text}`")
        raw_result_text = summarize_payload(action.get("raw_result"), limit=420)
        if raw_result_text and raw_result_text != observation_text:
            lines.append(f"- raw_result：`{raw_result_text}`")
        error_text = summarize_payload(action.get("error"), limit=240)
        if error_text:
            lines.append(f"- error 字段：`{error_text}`")
        feedbacks = [clean_text(item, limit=220) for item in action.get("feedbacks", []) if str(item).strip()]
        if feedbacks:
            lines.append(f"- 紧随其后的 runtime_feedback：`{' / '.join(feedbacks)}`")
        lines.append("")
        return lines

    if kind == "next":
        target = action.get("target")
        step_index = action.get("step_index")
        if action.get("valid"):
            lines.extend(
                [
                    f"### step {step_index} `NEXT {target}`",
                    "",
                    f"- 动作：phase 切换到 `{target}`",
                    f"- from -> to：`{action.get('from_phase')}` -> `{target}`",
                ]
            )
            thought = str(action.get("thought") or "").strip()
            if thought:
                lines.append(f"- executor thought：{thought}")
        else:
            lines.extend(
                [
                    f"### action `NEXT {target}`",
                    "",
                    f"- 动作：试图切换到 `{target}`，但被运行时拦下",
                ]
            )
        feedbacks = [clean_text(item, limit=220) for item in action.get("feedbacks", []) if str(item).strip()]
        if feedbacks:
            lines.append(f"- runtime_feedback：`{' / '.join(feedbacks)}`")
        lines.append("")
        return lines

    if kind == "answer":
        answer = action.get("answer") or "(empty)"
        header = f"### final `{answer}`" if action.get("valid") else f"### action `ANSWER {answer}`"
        lines.extend(
            [
                header,
                "",
                f"- 动作：在 `{final_phase_name}` 尝试输出答案 `{answer}`" if action.get("valid") else f"- 动作：尝试提前输出答案 `{answer}`",
            ]
        )
        thought = str(action.get("thought") or "").strip()
        if thought:
            lines.append(f"- executor thought：{thought}")
        feedbacks = [clean_text(item, limit=220) for item in action.get("feedbacks", []) if str(item).strip()]
        if feedbacks:
            lines.append(f"- runtime_feedback：`{' / '.join(feedbacks)}`")
        lines.append("")
        return lines

    lines.extend(
        [
            "### action",
            "",
            f"- {clean_text(action.get('text', ''), limit=220)}",
            "",
        ]
    )
    return lines


def build_doc(
    *,
    task_dir: Path,
    iteration_name: str,
    task_payload: dict,
    task_record: dict,
    state: dict,
    previous_summary: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    env_result = state.get("env_result", {})
    tools = env_result.get("tool_trajectory", [])
    transitions = env_result.get("phase_transitions", [])
    raw_output = env_result.get("raw_executor_output", "") or ""
    final_answer = str(env_result.get("final_answer") or "")
    executor_summary = str(env_result.get("executor_summary") or "")
    task_success = bool(task_record.get("task_success"))
    qid = str(task_record.get("original_question_id") or "").strip()
    doc_name = f"{task_dir.name}.md"
    doc_title = f"# {task_dir.name}"
    answer_phase = final_phase(transitions)
    gold_answer = str(state.get("task_context", {}).get("gold_answer") or task_payload.get("gold_answer") or "")
    parsed_actions = parse_actions(raw_output, tools, transitions)
    overview_lines = [render_action_overview(action, answer_phase) for action in parsed_actions]
    detail_lines: list[str] = []
    for action in parsed_actions:
        detail_lines.extend(render_action_detail(action, answer_phase))
    reason_lines = build_reason_lines(
        task_success=task_success,
        transitions=transitions,
        tools=tools,
        raw_output=raw_output,
        final_answer=final_answer,
        executor_summary=executor_summary,
    )

    invalid_phase_count = raw_output.count("Invalid phase transition.")
    unknown_phase_count = raw_output.count("Unknown phase.")
    invalid_answer_count = raw_output.count("Invalid answer output.")
    fallback_conclude = any("[fallback conclude]" in str(item.get("thought", "")) for item in transitions)
    resolved_conclude = any("[resolved conclude]" in str(item.get("thought", "")) for item in transitions)
    tool_failures = 0
    tool_degraded = 0
    degraded_tools: Counter[str] = Counter()
    failed_tools: Counter[str] = Counter()
    for item in tools:
        analysis = analyze_tool_record(item)
        tool_name = str(item.get("tool_name") or "")
        if analysis["status"] == "failed":
            tool_failures += 1
            failed_tools[tool_name] += 1
        elif analysis["status"] == "degraded":
            tool_degraded += 1
            degraded_tools[tool_name] += 1
    max_step = max(
        [0, *[int(item.get("step_index", 0)) for item in tools], *[int(item.get("step_index", 0)) for item in transitions]]
    )
    executor_action_count = sum(1 for action in parsed_actions if action.get("kind") != "feedback")

    lines = [
        doc_title,
        "",
        f"题号：`q{qid}`",
        "",
        f"入口文件：[task_record.json]({task_dir / 'task_record.json'}:1)",
        f"状态文件：[state.json]({task_dir / 'env/state.json'}:1)",
        f"任务文件：[task.json]({task_dir / 'task.json'}:1)",
        "",
        "## 题目",
        "",
        state.get("task_prompt", "").strip(),
        "",
        "## 选项",
        "",
        *render_choices(task_payload),
        "",
        "## 结果",
        "",
        f"- iteration：`{iteration_name}`",
        f"- modality / bucket：`{task_record.get('modality')}` / `{task_record.get('training_bucket')}`",
        f"- 是否成功：`{task_success}`",
        f"- 金标答案：`{gold_answer or '(empty)'}`",
        f"- 最终答案：`{final_answer or '(empty)'}`",
        f"- phase 路径：`{phase_path(transitions)}`",
        f"- 最终答题阶段：`{answer_phase}`",
        f"- accuracy / efficiency：`{task_record.get('evaluation', {}).get('accuracy')}` / `{task_record.get('evaluation', {}).get('efficiency')}`",
        f"- tool_exact_match / parameter_accuracy：`{task_record.get('evaluation', {}).get('tool_exact_match')}` / `{task_record.get('evaluation', {}).get('parameter_accuracy')}`",
        "",
        "## Step 节奏",
        "",
        f"- 记录到的最大 step：`{max_step}`",
        f"- executor 动作数：`{executor_action_count}`",
        f"- 工具调用数：`{len(tools)}`",
        f"- 工具失败数：`{tool_failures}`",
        f"- 错误样式返回数：`{tool_degraded}`",
        f"- 合法 phase 切换数：`{len(transitions)}`",
        f"- 非法 phase 跳转反馈：`{invalid_phase_count}`",
        f"- 未知 phase 反馈：`{unknown_phase_count}`",
        f"- 非 CONCLUDE 作答反馈：`{invalid_answer_count}`",
        f"- conclude 模式：`{'fallback' if fallback_conclude else 'resolved' if resolved_conclude else 'none'}`",
        f"- 是否有 env_result：`{bool(env_result)}`",
        "",
        "## Step 概览",
        "",
        *overview_lines,
        "",
        "## 详细执行轨迹",
        "",
        *detail_lines,
        "",
        "## 成败原因",
        "",
        *[f"- {item}" for item in reason_lines],
        "",
        "## 收尾摘要",
        "",
        f"`{clean_text(executor_summary or '(empty)', limit=260)}`",
        "",
    ]
    current_summary = {
        "task_dir_name": task_dir.name,
        "task_label": f"q{qid}",
        "doc_name": doc_name,
        "success": task_success,
        "phase_path": phase_path(transitions),
        "note": reason_lines[0] if reason_lines else "",
        "reason_lines": reason_lines,
        "gold_answer": gold_answer,
        "final_answer": final_answer,
        "degraded_count": tool_degraded,
        "failed_count": tool_failures,
        "degraded_tools": dict(degraded_tools),
        "failed_tools": dict(failed_tools),
    }
    lines.extend(render_iteration_compare_section(previous_summary, current_summary))
    return "\n".join(lines), current_summary


def build_incomplete_doc(
    *,
    task_dir: Path,
    iteration_name: str,
    task_payload: dict,
    previous_summary: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    qid = str(task_payload.get("task_id", "")).split("-")[-1] or "unknown"
    final_answer = ""
    gold_answer = str(task_payload.get("gold_answer") or "")
    reason_lines = [
        "生成复盘文档时该题还没有落出 `task_record.json`，按未完成失败处理。",
        "当前还没有 `env/state.json`，所以拿不到完整 executor 轨迹和工具返回。",
        "这类记录更接近运行中断或长尾未收尾，不代表已经形成了稳定错误答案。",
    ]
    lines = [
        f"# {task_dir.name}",
        "",
        f"题号：`q{qid}`",
        "",
        f"任务定义：[task.json]({task_dir / 'task.json'}:1)",
        f"task_record：`{(task_dir / 'task_record.json').exists()}`",
        f"state：`{(task_dir / 'env/state.json').exists()}`",
        "",
        "## 题目",
        "",
        str(task_payload.get("prompt") or "").strip(),
        "",
        "## 选项",
        "",
        *render_choices(task_payload),
        "",
        "## 结果",
        "",
        f"- iteration：`{iteration_name}`",
        "- modality / bucket：`(pending)` / `(pending)`",
        "- 是否成功：`False`",
        f"- 最终答案：`{final_answer or '(empty)'}`",
        "- phase 路径：`INIT -> (unfinished)`",
        "- 最终答题阶段：`(unfinished)`",
        "- accuracy / efficiency：`0` / `0`",
        "- tool_exact_match / parameter_accuracy：`0` / `0`",
        f"- 金标答案：`{gold_answer or '(empty)'}`",
        "",
        "## Step 节奏",
        "",
        "- 记录到的最大 step：`0`",
        "- executor 动作数：`0`",
        "- 工具调用数：`0`",
        "- 工具失败数：`0`",
        "- 错误样式返回数：`0`",
        "- 合法 phase 切换数：`0`",
        "- 非法 phase 跳转反馈：`0`",
        "- 未知 phase 反馈：`0`",
        "- 非 CONCLUDE 作答反馈：`0`",
        "- conclude 模式：`none`",
        "- 是否有 env_result：`False`",
        "",
        "## Step 概览",
        "",
        "- 生成文档时只存在 `task.json` / `task_bucket.json`，执行轨迹尚未落盘。",
        "- 按当前要求，这题在复盘目录中按失败计入。",
        "",
        "## 详细执行轨迹",
        "",
        "- 生成文档时只存在 `task.json` / `task_bucket.json`，执行轨迹尚未落盘。",
        "- 按当前要求，这题在复盘目录中按失败计入。",
        "",
        "## 成败原因",
        "",
        *[f"- {item}" for item in reason_lines],
        "",
        "## 收尾摘要",
        "",
        "`unfinished at doc generation time`",
        "",
    ]
    summary = {
        "task_dir_name": task_dir.name,
        "task_label": f"q{qid}",
        "doc_name": f"{task_dir.name}.md",
        "success": False,
        "phase_path": "INIT -> (unfinished)",
        "note": "未完成，按失败计入。",
        "reason_lines": reason_lines,
        "gold_answer": gold_answer,
        "final_answer": "",
        "degraded_count": 0,
        "failed_count": 0,
        "degraded_tools": {},
        "failed_tools": {},
    }
    lines.extend(render_iteration_compare_section(previous_summary, summary))
    return "\n".join(lines), summary


def write_iteration_readme(
    iteration_dir: Path,
    summaries: list[dict[str, object]],
    *,
    iteration_name: str,
    run_dir: Path,
) -> None:
    incomplete_count = sum(1 for item in summaries if "(unfinished)" in str(item.get("phase_path", "")))
    degraded_tasks = sum(1 for item in summaries if int(item.get("degraded_count", 0)) > 0)
    failed_tasks = sum(1 for item in summaries if int(item.get("failed_count", 0)) > 0)
    lines = [
        f"# {iteration_name} 逐题复盘",
        "",
        f"- 来源 run：`{run_dir}`",
        f"- 题目数：`{len(summaries)}`",
        f"- 未完成按失败计入：`{incomplete_count}`",
        f"- 含错误样式返回的题数：`{degraded_tasks}`",
        f"- 含显式失败调用的题数：`{failed_tasks}`",
        "",
        "| 题号 | 文档 | 成功 | phase 路径 | degraded | failed | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        note = clean_text(item["note"], limit=80)
        lines.append(
            f"| {item['task_label']} | [{item['task_dir_name']}]({item['doc_name']}) | `{item['success']}` | `{item['phase_path']}` | `{item.get('degraded_count', 0)}` | `{item.get('failed_count', 0)}` | {note} |"
        )
    lines.append("")
    (iteration_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_root_readme(
    output_dir: Path,
    *,
    run_dir: Path,
    run_name: str,
    report_path: Path | None,
    anomaly_report_name: str | None,
    iteration_summaries: dict[str, list[dict[str, object]]],
) -> None:
    lines = [
        "# 严格状态机两轮逐题复盘",
        "",
        f"- run name：`{run_name}`",
        f"- run dir：`{run_dir}`",
    ]
    if report_path is not None:
        lines.append(f"- 总报告：[训练报告]({report_path}:1)")
    lines.extend(
        [
            "",
            "## 目录",
            "",
            "- [iteration_01](iteration_01/README.md)",
            "- [iteration_02](iteration_02/README.md)",
            "- [两轮对错总表](两轮对错总表.md)",
        ]
    )
    if anomaly_report_name:
        lines.append(f"- [工具异常返回分析](../{anomaly_report_name})")
    lines.append("")
    for name, summaries in iteration_summaries.items():
        success_count = sum(1 for item in summaries if item["success"])
        incomplete_count = sum(1 for item in summaries if "(unfinished)" in str(item.get("phase_path", "")))
        degraded_tasks = sum(1 for item in summaries if int(item.get("degraded_count", 0)) > 0)
        failed_tasks = sum(1 for item in summaries if int(item.get("failed_count", 0)) > 0)
        lines.extend(
            [
                f"## {name}",
                "",
                f"- success：`{success_count}/{len(summaries)}`",
                f"- 未完成按失败计入：`{incomplete_count}`",
                f"- 含错误样式返回的题数：`{degraded_tasks}`",
                f"- 含显式失败调用的题数：`{failed_tasks}`",
                "",
            ]
        )
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def format_tool_counts(mapping: dict[str, object]) -> str:
    if not mapping:
        return "0"
    parts = []
    for name, count in sorted(mapping.items()):
        parts.append(f"{name} x{count}")
    return " / ".join(parts)


def write_cross_iteration_table(output_dir: Path, iteration_summaries: dict[str, list[dict[str, object]]]) -> None:
    iter1 = {item["task_label"]: item for item in iteration_summaries.get("iteration_01", [])}
    iter2 = {item["task_label"]: item for item in iteration_summaries.get("iteration_02", [])}
    labels = [item["task_label"] for item in iteration_summaries.get("iteration_01", [])]
    for item in iteration_summaries.get("iteration_02", []):
        if item["task_label"] not in labels:
            labels.append(item["task_label"])

    both_right = 0
    both_wrong = 0
    recovered = 0
    regressed = 0
    rows: list[str] = []

    for idx, label in enumerate(labels, 1):
        prev = iter1.get(label, {})
        cur = iter2.get(label, {})
        prev_success = bool(prev.get("success"))
        cur_success = bool(cur.get("success"))
        if prev_success and cur_success:
            change = "持平"
            both_right += 1
        elif (not prev_success) and (not cur_success):
            change = "持平"
            both_wrong += 1
        elif (not prev_success) and cur_success:
            change = "捞回"
            recovered += 1
        else:
            change = "回落"
            regressed += 1
        prev_label = "对" if prev_success else "错"
        cur_label = "对" if cur_success else "错"
        if "(unfinished)" in str(cur.get("phase_path", "")):
            cur_label += "（未完成）"
        rows.append(
            f"| {idx:02d} | {label} | {prev_label} | {cur_label} | {change} | `{prev.get('degraded_count', 0)}/{prev.get('failed_count', 0)}` | `{cur.get('degraded_count', 0)}/{cur.get('failed_count', 0)}` | [iter1](iteration_01/{prev.get('doc_name')}) / [iter2](iteration_02/{cur.get('doc_name')}) |"
        )

    lines = [
        "# 两轮对错总表",
        "",
        f"- `iteration_01`：`{sum(1 for item in iter1.values() if item.get('success'))}/{len(iter1)}`",
        f"- `iteration_02`：`{sum(1 for item in iter2.values() if item.get('success'))}/{len(iter2)}`",
        f"- 两轮都对：`{both_right}`",
        f"- 两轮都错：`{both_wrong}`",
        f"- 第二轮捞回：`{recovered}`",
        f"- 第二轮回落：`{regressed}`",
        "- `degraded/failed` 列的口径：前者表示表面 success 但返回内容带错误信号，后者表示运行时已经记成失败。",
        "",
        "| 序号 | 题号 | iteration_01 | iteration_02 | 变化 | iter1 degraded/failed | iter2 degraded/failed | 文档 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
    ]
    (output_dir / "两轮对错总表.md").write_text("\n".join(lines), encoding="utf-8")


def write_tool_anomaly_summary(
    output_path: Path,
    *,
    run_dir: Path,
    iteration_summaries: dict[str, list[dict[str, object]]],
) -> None:
    lines = [
        "# 工具异常返回分析",
        "",
        f"- run dir：`{run_dir}`",
        "- 统计口径：`degraded` 表示 `success=true`，但 `observation/raw_result/stdout` 已经带出明显错误信号；`failed` 表示运行时已经把这次调用记成失败。",
        "",
        "## 总量",
        "",
        "| iteration | success | degraded steps | degraded tasks | failed steps | failed tasks |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    anomaly_rows: list[tuple[str, dict[str, object]]] = []
    total_degraded_steps = 0
    total_failed_steps = 0

    for iteration_name in ("iteration_01", "iteration_02"):
        summaries = iteration_summaries.get(iteration_name, [])
        success_count = sum(1 for item in summaries if item.get("success"))
        degraded_steps = sum(int(item.get("degraded_count", 0)) for item in summaries)
        failed_steps = sum(int(item.get("failed_count", 0)) for item in summaries)
        degraded_tasks = sum(1 for item in summaries if int(item.get("degraded_count", 0)) > 0)
        failed_tasks = sum(1 for item in summaries if int(item.get("failed_count", 0)) > 0)
        total_degraded_steps += degraded_steps
        total_failed_steps += failed_steps
        lines.append(
            f"| {iteration_name} | `{success_count}/{len(summaries)}` | `{degraded_steps}` | `{degraded_tasks}` | `{failed_steps}` | `{failed_tasks}` |"
        )
        for item in summaries:
            if int(item.get("degraded_count", 0)) > 0 or int(item.get("failed_count", 0)) > 0:
                anomaly_rows.append((iteration_name, item))

    lines.extend(
        [
            "",
            f"- 两轮合计 `degraded` step：`{total_degraded_steps}`",
            f"- 两轮合计 `failed` step：`{total_failed_steps}`",
            "",
            "## 受影响任务",
            "",
            "| iteration | 题号 | 成功 | degraded | failed | degraded tools | failed tools | 文档 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for iteration_name, item in anomaly_rows:
        lines.append(
            f"| {iteration_name} | {item['task_label']} | `{item['success']}` | `{item.get('degraded_count', 0)}` | `{item.get('failed_count', 0)}` | {format_tool_counts(item.get('degraded_tools', {}))} | {format_tool_counts(item.get('failed_tools', {}))} | [{item['task_dir_name']}]({iteration_name}/{item['doc_name']}) |"
        )

    lines.extend(
        [
            "",
            "## 共同根因",
            "",
            f"- [Perception.py](/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Perception.py) 的 `_norm_path()` 只处理带前导斜杠的 `\"/benchmark/data/\"`。`model_results.csv` 里的相对路径是 `benchmark/data/...`，所以单图感知工具经常在匹配阶段就落空。",
            f"- 同一个 [Perception.py](/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Perception.py) 里，`SM3Det` / `InstructSAM` / `RemoteSAM` / `SAM2` 的结果解包也有第二层问题。代码先取 `matches.values[0]`，拿到的是 ndarray，后面又用文本 prompt 或 bbox 去索引，这条路径从代码上看也会掉进 `except` 并回到 `\"Failed to call model\"`。这里是基于源码和 `model_results.csv` 行格式做的判断。",
            f"- `ChangeOS` 还有额外的 pair-path 错配。[Perception.py](/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Perception.py) 只拿 `pre_image_path` 去匹配，`model_results.csv` 里的键却是 `(pre, post)` 二元组，所以 `q223` / `q225` 这类题会稳定退化。",
            f"- [agent_loop.py](/data/xsy/project_skills-3.18dhc-19.40/nlrl_skills/agent_loop.py) 现在把 success 的语义定义成“没有抛异常，或 subprocess `returncode` 为 0”。像 `\"Failed to call model\"` 这种失败字符串，或者 `stdout` 里打印 `{{\"error\": ...}}` 但进程正常退出的脚本，都会被记成 success。",
            f"- [critic.py](/data/xsy/project_skills-3.18dhc-19.40/nlrl_skills/critic.py) 传给 critic 的 `tool_trajectory` 只保留 `tool / success / error`。很多 `degraded` case 没有 `error` 字段，critic 看到的是“工具成功调用”，训练反馈会被带偏。",
            "",
            "## 对工作流的影响",
            "",
            "- executor 有时能从 observation 文本里意识到工具坏了，于是转去猜答案或提前收尾。",
            "- runtime 本身不会把这类 case 视作失败，不会触发真正的失败分支、失败统计或更强的回退逻辑。",
            "- 逐题复盘如果只看 `success` 字段，会把假成功写成“成功”，所以这次重跑文档时把 `degraded` 单独拆出来了。",
            "",
        ]
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def latest_training_report(report_root: Path, run_name: str) -> Path | None:
    matches = sorted(report_root.glob(f"*{run_name}*严格状态机训练报告.md"))
    return matches[-1] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate per-task strict-state case docs for iteration_01 and iteration_02.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--report-root", default="/data/xsy/project_skills-3.18dhc-19.40/实验设计与迭代")
    parser.add_argument("--output-name", default="")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    report_root = Path(args.report_root)
    run_name = run_dir.name
    output_name = args.output_name or f"{now_stamp()}_严格状态机两轮30题逐题复盘"
    output_dir = report_root / output_name
    anomaly_report_path = report_root / f"{output_name}_工具异常返回分析.md"
    output_dir.mkdir(parents=True, exist_ok=True)

    iteration_summaries: dict[str, list[dict[str, object]]] = {}

    for iteration_name in ("iteration_01", "iteration_02"):
        source_iteration_dir = run_dir / iteration_name
        target_iteration_dir = output_dir / iteration_name
        target_iteration_dir.mkdir(parents=True, exist_ok=True)
        summaries: list[dict[str, object]] = []
        previous_by_label = {
            item["task_label"]: item for item in iteration_summaries.get("iteration_01", [])
        } if iteration_name == "iteration_02" else {}

        task_dirs = sorted(path for path in source_iteration_dir.iterdir() if path.is_dir() and path.name.startswith("task_"))
        for task_dir in task_dirs:
            task_payload = load_json(task_dir / "task.json")
            task_record = load_json_if_exists(task_dir / "task_record.json")
            state = load_json_if_exists(task_dir / "env/state.json")
            qid = str(task_payload.get("task_id", "")).split("-")[-1] or "unknown"
            previous_summary = previous_by_label.get(f"q{qid}")
            if task_record is not None and state is not None:
                content, summary = build_doc(
                    task_dir=task_dir,
                    iteration_name=iteration_name,
                    task_payload=task_payload,
                    task_record=task_record,
                    state=state,
                    previous_summary=previous_summary,
                )
            else:
                content, summary = build_incomplete_doc(
                    task_dir=task_dir,
                    iteration_name=iteration_name,
                    task_payload=task_payload,
                    previous_summary=previous_summary,
                )
            (target_iteration_dir / f"{task_dir.name}.md").write_text(content, encoding="utf-8")
            summaries.append(summary)

        iteration_summaries[iteration_name] = summaries
        write_iteration_readme(
            target_iteration_dir,
            summaries,
            iteration_name=iteration_name,
            run_dir=run_dir,
        )

    write_root_readme(
        output_dir,
        run_dir=run_dir,
        run_name=run_name,
        report_path=latest_training_report(report_root, run_name),
        anomaly_report_name=anomaly_report_path.name,
        iteration_summaries=iteration_summaries,
    )
    write_cross_iteration_table(output_dir, iteration_summaries)
    write_tool_anomaly_summary(
        anomaly_report_path,
        run_dir=run_dir,
        iteration_summaries=iteration_summaries,
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
