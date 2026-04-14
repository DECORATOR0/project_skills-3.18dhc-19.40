#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

FINAL_STATUSES = {
    "finished",
    "stopped",
    "stopped_partial",
    "dead",
}

SPOT_CHECK_TASKS = {
    "earth-bench-c-42",
    "earth-bench-c-45",
    "earth-bench-c-59",
    "earth-bench-c-205",
    "earth-bench-c-223",
    "earth-bench-c-225",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_datetime(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed
        except ValueError:
            pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%a %b %d %H:%M:%S %Y",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed
        except ValueError:
            continue
    return None


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return fieldnames, rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def get_process_info(pid: int) -> tuple[str, str] | None:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout.strip()
    if proc.returncode != 0 or not output:
        return None
    tokens = output.split()
    if len(tokens) < 6:
        return None
    stat = tokens[-1]
    start_text = " ".join(tokens[:-1])
    return start_text, stat


def process_matches_start(recorded_start: str, actual_start: str) -> bool:
    recorded = parse_datetime(recorded_start)
    actual = parse_datetime(actual_start)
    if recorded is None or actual is None:
        return True
    return abs((recorded - actual).total_seconds()) <= 5


def read_log_tail(path: Path, max_chars: int = 16000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def infer_terminal_status(run_dir: Path, launch_log: Path) -> str:
    if (run_dir / "run_summary.json").exists():
        return "finished"
    log_tail = read_log_tail(launch_log)
    if "train_exit status=0" in log_tail:
        return "finished"
    return "dead"


def update_registry_row(
    csv_path: Path,
    *,
    run_name: str,
    pid: int,
    status: str,
    checked_at: str,
    ended_at: str = "",
    report_path: str = "",
) -> None:
    fieldnames, rows = read_rows(csv_path)
    for row in rows:
        if (row.get("name") or "").strip() != run_name:
            continue
        if (row.get("pid") or "").strip() != str(pid):
            continue
        row["last_checked_at"] = checked_at
        row["status"] = status
        if ended_at:
            row["ended_at"] = ended_at
        if report_path:
            notes = (row.get("notes") or "").strip()
            note_line = f"report={report_path}"
            if note_line not in notes:
                row["notes"] = f"{notes}; {note_line}".strip("; ")
        break
    write_rows(csv_path, fieldnames, rows)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_phase_path(state: dict) -> str:
    transitions = state.get("env_result", {}).get("phase_transitions", [])
    phases = ["INIT"]
    phases.extend(t.get("to_phase", "") for t in transitions if t.get("to_phase"))
    if state.get("env_result", {}).get("final_answer"):
        phases.append("ANSWER")
    return " -> ".join(phases)


def collect_iteration_stats(iteration_dir: Path) -> dict:
    state_paths = sorted(iteration_dir.glob("task_*/env/state.json"))
    path_counter: Counter[str] = Counter()
    fallback_count = 0
    resolved_count = 0
    invalid_phase_count = 0
    unknown_phase_count = 0
    invalid_answer_count = 0
    empty_answer_count = 0
    spot_checks: dict[str, dict] = {}

    for state_path in state_paths:
        state = load_json(state_path)
        env_result = state.get("env_result", {})
        transitions = env_result.get("phase_transitions", [])
        raw_output = env_result.get("raw_executor_output", "")
        final_answer = env_result.get("final_answer", "")
        task_success = bool(env_result.get("evaluation", {}).get("task_success"))

        path = build_phase_path(state)
        path_counter[path] += 1
        fallback_count += int(any("[fallback conclude]" in (item.get("thought") or "") for item in transitions))
        resolved_count += int(any("[resolved conclude]" in (item.get("thought") or "") for item in transitions))
        invalid_phase_count += raw_output.count("Invalid phase transition.")
        unknown_phase_count += raw_output.count("Unknown phase.")
        invalid_answer_count += raw_output.count("Invalid answer output.")
        empty_answer_count += int(not final_answer)

        task_id = state.get("task_id", "")
        if task_id in SPOT_CHECK_TASKS:
            spot_checks[task_id] = {
                "path": path,
                "answer": final_answer or "(empty)",
                "success": task_success,
                "fallback": any("[fallback conclude]" in (item.get("thought") or "") for item in transitions),
            }

    summary = {}
    summary_path = iteration_dir / "iteration_summary.json"
    if summary_path.exists():
        summary = load_json(summary_path)

    return {
        "iteration_name": iteration_dir.name,
        "task_count": len(state_paths),
        "success_count": int(summary.get("success_count", 0)),
        "success_rate": summary.get("success_rate", 0),
        "accuracy": summary.get("training_metrics", {}).get("avg_metrics", {}).get("accuracy", 0),
        "fallback_count": fallback_count,
        "resolved_count": resolved_count,
        "invalid_phase_count": invalid_phase_count,
        "unknown_phase_count": unknown_phase_count,
        "invalid_answer_count": invalid_answer_count,
        "empty_answer_count": empty_answer_count,
        "path_counter": path_counter,
        "spot_checks": spot_checks,
    }


def is_training_iteration(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not path.name.startswith("iteration_"):
        return False
    suffix = path.name.split("_", 1)[1]
    return suffix.isdigit() and int(suffix) >= 1


def build_report(run_name: str, run_dir: Path, launch_log: Path, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%y.%-m.%-d_%H%M")
    report_path = report_dir / f"{stamp}_{run_name}_严格状态机训练报告.md"

    run_summary = load_json(run_dir / "run_summary.json") if (run_dir / "run_summary.json").exists() else {}
    iterations = sorted(path for path in run_dir.glob("iteration_*") if is_training_iteration(path))
    iteration_stats = [collect_iteration_stats(path) for path in iterations]

    overall_path_counter: Counter[str] = Counter()
    total_fallback = 0
    total_resolved = 0
    total_invalid_phase = 0
    total_unknown_phase = 0
    total_invalid_answer = 0
    total_empty_answer = 0
    overall_tasks = 0
    spot_check_matrix: dict[str, dict[str, dict]] = {}

    for item in iteration_stats:
        overall_path_counter.update(item["path_counter"])
        total_fallback += item["fallback_count"]
        total_resolved += item["resolved_count"]
        total_invalid_phase += item["invalid_phase_count"]
        total_unknown_phase += item["unknown_phase_count"]
        total_invalid_answer += item["invalid_answer_count"]
        total_empty_answer += item["empty_answer_count"]
        overall_tasks += item["task_count"]
        for task_id, payload in item["spot_checks"].items():
            spot_check_matrix.setdefault(task_id, {})[item["iteration_name"]] = payload

    log_tail = read_log_tail(launch_log, max_chars=6000)
    lines: list[str] = [
        f"# 严格状态机两轮训练报告",
        "",
        "## 本次运行",
        f"- 运行名：`{run_name}`",
        f"- 运行目录：`{run_dir}`",
        f"- 启动日志：`{launch_log}`",
        f"- 报告生成时间：`{now_iso()}`",
        "",
        "## 总览",
        f"- 训练完成轮数：`{run_summary.get('iterations_completed', 0)}/{run_summary.get('iterations_per_batch', 0)}`",
        f"- 最终 success：`{run_summary.get('final_success_count', 0)}/{run_summary.get('batch_size', 0)}`",
        f"- fallback conclude 总数：`{total_fallback}/{overall_tasks}`",
        f"- resolved conclude 总数：`{total_resolved}/{overall_tasks}`",
        f"- 非法 phase 跳转反馈次数：`{total_invalid_phase}`",
        f"- 未知 phase 反馈次数：`{total_unknown_phase}`",
        f"- 非 CONCLUDE 作答反馈次数：`{total_invalid_answer}`",
        f"- 空答案数：`{total_empty_answer}`",
        "",
    ]

    if run_summary.get("iteration_summaries"):
        lines.extend([
            "## iteration 结果",
        ])
        for item in run_summary["iteration_summaries"]:
            lines.append(
                f"- iteration_{int(item.get('iteration_index', 0)):02d}：success `{item.get('success_count', 0)}/{item.get('task_count', 0)}`，accuracy `{item.get('training_metrics', {}).get('avg_metrics', {}).get('accuracy', 0)}`"
            )
        lines.append("")

    lines.append("## phase 路径分布")
    for path, count in overall_path_counter.most_common():
        lines.append(f"- `{path}`：{count}")
    lines.append("")

    lines.append("## 分轮细节")
    for item in iteration_stats:
        lines.append(f"### {item['iteration_name']}")
        lines.append(f"- success：`{item['success_count']}/{item['task_count']}`")
        lines.append(f"- accuracy：`{item['accuracy']}`")
        lines.append(f"- fallback conclude：`{item['fallback_count']}/{item['task_count']}`")
        lines.append(f"- resolved conclude：`{item['resolved_count']}/{item['task_count']}`")
        lines.append(f"- 非法 phase 跳转反馈次数：`{item['invalid_phase_count']}`")
        lines.append(f"- 未知 phase 反馈次数：`{item['unknown_phase_count']}`")
        lines.append(f"- 非 CONCLUDE 作答反馈次数：`{item['invalid_answer_count']}`")
        lines.append(f"- 空答案数：`{item['empty_answer_count']}`")
        lines.append("- 路径分布：")
        for path, count in item["path_counter"].most_common():
            lines.append(f"`{path}`：{count}")
        lines.append("")

    lines.append("## spot check")
    for task_id in sorted(SPOT_CHECK_TASKS):
        lines.append(f"### {task_id}")
        task_rows = spot_check_matrix.get(task_id, {})
        if not task_rows:
            lines.append("- 本次运行里没有采到该题的 state.json。")
            continue
        for iteration_name in sorted(task_rows):
            payload = task_rows[iteration_name]
            lines.append(
                f"- {iteration_name}：path `{payload['path']}`，answer `{payload['answer']}`，success `{payload['success']}`，fallback `{payload['fallback']}`"
            )
    lines.append("")

    lines.extend([
        "## 启动日志尾部",
        "```text",
        log_tail.strip(),
        "```",
        "",
    ])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll a batch30 train run, update 活的进程.csv, and write a report when done.")
    parser.add_argument("--csv-path", default="/data/xsy/活的进程.csv")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--launch-log", required=True)
    parser.add_argument("--poll-seconds", type=int, default=600)
    parser.add_argument(
        "--report-dir",
        default="/data/xsy/project_skills-3.18dhc-19.40/实验设计与迭代",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    run_dir = Path(args.run_dir)
    launch_log = Path(args.launch_log)
    report_dir = Path(args.report_dir)

    while True:
        checked_at = now_iso()
        process_info = get_process_info(args.pid)
        if process_info is not None and process_matches_start(args.start_time, process_info[0]):
            update_registry_row(
                csv_path,
                run_name=args.run_name,
                pid=args.pid,
                status="running",
                checked_at=checked_at,
            )
            time.sleep(args.poll_seconds)
            continue

        terminal_status = infer_terminal_status(run_dir, launch_log)
        ended_at = now_iso()
        report_path = ""
        if (run_dir / "run_summary.json").exists():
            report_path = str(build_report(args.run_name, run_dir, launch_log, report_dir))
        update_registry_row(
            csv_path,
            run_name=args.run_name,
            pid=args.pid,
            status=terminal_status,
            checked_at=checked_at,
            ended_at=ended_at,
            report_path=report_path,
        )
        print(
            json.dumps(
                {
                    "run_name": args.run_name,
                    "status": terminal_status,
                    "checked_at": checked_at,
                    "ended_at": ended_at,
                    "report_path": report_path,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
