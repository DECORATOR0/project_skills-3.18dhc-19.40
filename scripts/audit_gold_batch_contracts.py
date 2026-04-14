#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# Same structural problem as q42:
# gold trajectory sends list arguments to EO generation tools whose current runtime
# implementations are scalar-only and therefore cannot execute the gold batch call directly.
SCALAR_EO_TOOLS = {
    "ATI": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:983",
    "band_ratio": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:44",
    "compute_tvdi": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Index.py:1036",
    "lst_single_channel": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:152",
    "lst_multi_channel": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:245",
    "modis_day_night_lst": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:584",
    "split_window": "/data/xsy/project_skills-3.18dhc-19.40/agent/tools/Inversion.py:328",
}


def load_tasks(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["tasks"]


def find_conflicts(tasks: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    by_tool: dict[str, list[dict]] = defaultdict(list)
    all_rows: list[dict] = []

    for task in tasks:
        qid = str(task.get("metadata", {}).get("original_question_id", ""))
        for step_index, msg in enumerate(task.get("gold_trajectory") or [], start=1):
            if msg.get("role") != "assistant":
                continue
            for tool_call in msg.get("tool_calls") or []:
                function = tool_call.get("function", {})
                tool_name = function.get("name")
                if tool_name not in SCALAR_EO_TOOLS:
                    continue
                arguments = function.get("arguments", {})
                list_args = [
                    {
                        "name": key,
                        "len": len(value),
                    }
                    for key, value in arguments.items()
                    if isinstance(value, list)
                ]
                if not list_args:
                    continue
                row = {
                    "task_id": task["task_id"],
                    "qid": qid,
                    "tool": tool_name,
                    "step_index": step_index,
                    "list_args": list_args,
                }
                by_tool[tool_name].append(row)
                all_rows.append(row)

    return by_tool, all_rows


def format_qids(rows: list[dict]) -> str:
    qids = sorted({int(row["qid"]) for row in rows})
    return ", ".join(f"q{qid}" for qid in qids)


def build_markdown(
    *,
    by_tool: dict[str, list[dict]],
    all_rows: list[dict],
    questions_path: Path,
) -> str:
    unique_tasks = sorted({(int(row["qid"]), row["task_id"]) for row in all_rows})
    total_tasks = 248
    ratio = len(unique_tasks) / total_tasks if total_tasks else 0.0

    lines = [
        "# 248题 Gold Batch 契约冲突扫描",
        "",
        "## 口径",
        "- 只统计和 `q42` 同构的问题。",
        "- 判定条件：gold trajectory 对当前 runtime 的单值 EO 生成工具直接传 list 参数。",
        "- 不统计本来就应该吃 list 的聚合/几何辅助工具。",
        "- 不统计其他类型的 gold/runtime 不一致，例如工具名或参数名改写问题。",
        "",
        "## 结果总览",
        f"- 扫描数据：`{questions_path}`",
        f"- 命中题数：`{len(unique_tasks)}/248`",
        f"- 占比：`{ratio:.2%}`",
        f"- 命中 gold tool_call 数：`{len(all_rows)}`",
        "",
        "## 按工具分组",
    ]

    for tool_name in sorted(by_tool):
        rows = by_tool[tool_name]
        unique_count = len({row["task_id"] for row in rows})
        lines.extend(
            [
                f"### {tool_name}",
                f"- 当前实现位置：[{tool_name}]({SCALAR_EO_TOOLS[tool_name]})",
                f"- 命中题数：`{unique_count}`",
                f"- 题号：{format_qids(rows)}",
                "",
            ]
        )

    lines.extend(
        [
            "## 全部题号",
            "",
            format_qids(all_rows),
            "",
            "## 说明",
            "- 这份报告反映的是 gold 设计和当前 runtime 工具契约之间的结构性冲突。",
            "- 这些题即使 executor 理解了“应该全时段 batch 处理”，当前工具层也不一定能直接执行 gold 写法。",
            "- `calculate_threshold_ratio` 这类下游聚合工具支持 list，不在本报告冲突范围内；冲突出在上游生成工具。",
            "",
        ]
    )

    return "\n".join(lines)


def default_output_name() -> str:
    return datetime.now().strftime("%y.%-m.%-d_%H%M_248题gold_batch契约冲突扫描.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 248-task gold trajectories for q42-style batch/tool contract conflicts.")
    parser.add_argument(
        "--questions-path",
        default="/data/xsy/project_skills-3.18dhc-19.40/data/converted/earth_bench_skill_rl/question.json",
    )
    parser.add_argument(
        "--report-dir",
        default="/data/xsy/project_skills-3.18dhc-19.40/实验设计与迭代",
    )
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    questions_path = Path(args.questions_path)
    tasks = load_tasks(questions_path)
    by_tool, all_rows = find_conflicts(tasks)

    payload = {
        "unique_task_count": len({row["task_id"] for row in all_rows}),
        "tool_call_count": len(all_rows),
        "by_tool": {
            tool: {
                "task_count": len({row["task_id"] for row in rows}),
                "qids": sorted({int(row["qid"]) for row in rows}),
            }
            for tool, rows in sorted(by_tool.items())
        },
        "qids": sorted({int(row["qid"]) for row in all_rows}),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.write_report:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / default_output_name()
        report_path.write_text(
            build_markdown(by_tool=by_tool, all_rows=all_rows, questions_path=questions_path),
            encoding="utf-8",
        )
        print(report_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
