#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


ACTION_PATTERN = re.compile(
    r"(?P<call><CALL>(?P<call_name>.*?)</CALL><ARGS>(?P<call_args>.*?)</ARGS>)"
    r"|(?P<next><NEXT>(?P<next_name>.*?)</NEXT>)"
    r"|(?P<answer><ANSWER>(?P<answer_text>.*?)</ANSWER>)"
    r"|(?P<feedback>\[runtime_feedback\]\s*(?P<feedback_text>.*?))(?=(?:\s*<THOUGHT>|\s*<CALL>|\s*<NEXT>|\s*<ANSWER>|\Z))",
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


def summarize_observation(observation: object) -> str:
    return clean_text(observation, limit=180)


def observation_is_error_like(observation: object) -> bool:
    text = clean_text(observation, limit=500).lower()
    return any(marker in text for marker in ERROR_LIKE_MARKERS)


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
            observation = tool.get("observation", "")
            status = "success"
            if not tool.get("success", False):
                status = "failed"
            elif observation_is_error_like(observation):
                status = "degraded"
            actions.append(
                {
                    "kind": "call",
                    "tool_name": match.group("call_name").strip(),
                    "step_index": tool.get("step_index"),
                    "status": status,
                    "observation": observation,
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
                    "thought": transition.get("thought") if valid else "",
                    "feedbacks": [],
                }
            )
            continue

        if match.group("answer"):
            actions.append(
                {
                    "kind": "answer",
                    "answer": clean_text(match.group("answer_text"), limit=80),
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


def render_action(action: dict[str, object], final_phase_name: str) -> str:
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
            detail = f"。返回：`{summarize_observation(action.get('observation', ''))}`"
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


def build_doc(
    *,
    task_dir: Path,
    iteration_name: str,
    task_record: dict,
    state: dict,
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
    action_lines = [render_action(action, answer_phase) for action in parse_actions(raw_output, tools, transitions)]
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
    tool_failures = sum(1 for item in tools if not item.get("success", False))
    tool_degraded = sum(1 for item in tools if item.get("success", False) and observation_is_error_like(item.get("observation", "")))
    max_step = max(
        [0, *[int(item.get("step_index", 0)) for item in tools], *[int(item.get("step_index", 0)) for item in transitions]]
    )

    lines = [
        doc_title,
        "",
        f"题号：`q{qid}`",
        "",
        f"入口文件：[task_record.json]({task_dir / 'task_record.json'}:1)",
        f"状态文件：[state.json]({task_dir / 'env/state.json'}:1)",
        "",
        "## 题目",
        "",
        state.get("task_prompt", "").strip(),
        "",
        "## 结果",
        "",
        f"- iteration：`{iteration_name}`",
        f"- modality / bucket：`{task_record.get('modality')}` / `{task_record.get('training_bucket')}`",
        f"- 是否成功：`{task_success}`",
        f"- 最终答案：`{final_answer or '(empty)'}`",
        f"- phase 路径：`{phase_path(transitions)}`",
        f"- 最终答题阶段：`{answer_phase}`",
        f"- accuracy / efficiency：`{task_record.get('evaluation', {}).get('accuracy')}` / `{task_record.get('evaluation', {}).get('efficiency')}`",
        f"- tool_exact_match / parameter_accuracy：`{task_record.get('evaluation', {}).get('tool_exact_match')}` / `{task_record.get('evaluation', {}).get('parameter_accuracy')}`",
        "",
        "## Step 节奏",
        "",
        f"- 记录到的最大 step：`{max_step}`",
        f"- 工具调用数：`{len(tools)}`",
        f"- 工具失败数：`{tool_failures}`",
        f"- 错误样式返回数：`{tool_degraded}`",
        f"- 合法 phase 切换数：`{len(transitions)}`",
        f"- 非法 phase 跳转反馈：`{invalid_phase_count}`",
        f"- 未知 phase 反馈：`{unknown_phase_count}`",
        f"- 非 CONCLUDE 作答反馈：`{invalid_answer_count}`",
        f"- conclude 模式：`{'fallback' if fallback_conclude else 'resolved' if resolved_conclude else 'none'}`",
        "",
        "## Step 行为细节",
        "",
        *action_lines,
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

    summary = {
        "task_dir_name": task_dir.name,
        "task_label": f"q{qid}",
        "doc_name": doc_name,
        "success": task_success,
        "phase_path": phase_path(transitions),
        "note": reason_lines[0] if reason_lines else "",
    }
    return "\n".join(lines), summary


def write_iteration_readme(
    iteration_dir: Path,
    summaries: list[dict[str, object]],
    *,
    iteration_name: str,
    run_dir: Path,
) -> None:
    lines = [
        f"# {iteration_name} 逐题复盘",
        "",
        f"- 来源 run：`{run_dir}`",
        f"- 题目数：`{len(summaries)}`",
        "",
        "| 题号 | 文档 | 成功 | phase 路径 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        note = clean_text(item["note"], limit=80)
        lines.append(
            f"| {item['task_label']} | [{item['task_dir_name']}]({item['doc_name']}) | `{item['success']}` | `{item['phase_path']}` | {note} |"
        )
    lines.append("")
    (iteration_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_root_readme(
    output_dir: Path,
    *,
    run_dir: Path,
    run_name: str,
    report_path: Path | None,
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
            "",
        ]
    )
    for name, summaries in iteration_summaries.items():
        success_count = sum(1 for item in summaries if item["success"])
        lines.extend(
            [
                f"## {name}",
                "",
                f"- success：`{success_count}/{len(summaries)}`",
                "",
            ]
        )
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


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
    output_dir.mkdir(parents=True, exist_ok=True)

    iteration_summaries: dict[str, list[dict[str, object]]] = {}

    for iteration_name in ("iteration_01", "iteration_02"):
        source_iteration_dir = run_dir / iteration_name
        target_iteration_dir = output_dir / iteration_name
        target_iteration_dir.mkdir(parents=True, exist_ok=True)
        summaries: list[dict[str, object]] = []

        task_dirs = sorted(path for path in source_iteration_dir.iterdir() if path.is_dir() and path.name.startswith("task_"))
        for task_dir in task_dirs:
            task_record = load_json(task_dir / "task_record.json")
            state = load_json(task_dir / "env/state.json")
            content, summary = build_doc(
                task_dir=task_dir,
                iteration_name=iteration_name,
                task_record=task_record,
                state=state,
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
        iteration_summaries=iteration_summaries,
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
