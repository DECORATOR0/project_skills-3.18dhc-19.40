#!/usr/bin/env python3
from __future__ import annotations

import json
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


PROJECT_ROOT = Path("/data/xsy/project_skills-3.18dhc-19.40").resolve()
PYTHON_BIN = Path("/data/xsy/miniconda3/envs/earth-bench-skill-eval/bin/python")
RUN_PIPELINE = PROJECT_ROOT / "scripts" / "run_pipeline.py"
DOC_PATH = PROJECT_ROOT / "实验设计与迭代" / "20260401_183442_origin_dhc_skillpool_六优良梳理.md"

SOURCE_RUN_DIR = PROJECT_ROOT / "runs" / "train_task_local_parallel_all248_20260404_v5_success107_agg_input_20260405"
TASK_FILE = PROJECT_ROOT / "runs" / "train_task_local_parallel_all248_20260404_v5" / "completed140_task_ids.txt"

AGG_CONFIG = PROJECT_ROOT / "configs" / "system.train_local_actor_critic_sssai_gpt52.json"
EVAL_CONFIG = PROJECT_ROOT / "configs" / "system.eval.local_qwen3_8b_stream140_gpu0_isolated.json"

AGG_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "train_task_local_parallel_all248_20260404_v5" / "aggregated_skill_library_6_v5_success107_gpt52api_20260405_r1"
EVAL_RUN_ROOT = PROJECT_ROOT / "runs_gpu0"
EVAL_RUN_NAME = "eval_aggregated6_v5success107_gpt52api_localqwen3_gpu0_same140_c20_20260405_r1"
EVAL_RUN_DIR = EVAL_RUN_ROOT / EVAL_RUN_NAME

MONITOR_LOG = PROJECT_ROOT / "runs_gpu0" / "_orchestration_v5success107_gpt52api_same140_gpu0_20260405_r1.log"
STATUS_JSON = PROJECT_ROOT / "runs_gpu0" / "_orchestration_v5success107_gpt52api_same140_gpu0_20260405_r1.status.json"
SUMMARY_JSON = PROJECT_ROOT / "runs_gpu0" / "_orchestration_v5success107_gpt52api_same140_gpu0_20260405_r1.summary.json"

MONITOR_INTERVAL_SECONDS = 600
POLL_SECONDS = 30
EXPECTED_FAMILIES = 6
EXPECTED_TASKS = 140


@dataclass(frozen=True)
class FormalTestRow:
    stage: str
    training_model: str
    aggregation_method: str
    source_count: int
    test_set: str
    success_count: int
    accuracy: float
    note: str


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def log(message: str) -> None:
    ensure_parent(MONITOR_LOG)
    with MONITOR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp()}] {message}\n")


def write_status(payload: dict) -> None:
    ensure_parent(STATUS_JSON)
    STATUS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def count_aggregated_families() -> tuple[int, list[str]]:
    family_paths = sorted(path.parent.name for path in AGG_OUTPUT_ROOT.glob("earth-*/SKILL.md"))
    return len(family_paths), family_paths


def count_evaluated_tasks() -> tuple[int, list[str]]:
    task_paths = sorted(path.parent.name for path in EVAL_RUN_DIR.glob("task_*/evaluation_summary.json"))
    return len(task_paths), task_paths[-5:]


def stream_subprocess_output(proc: subprocess.Popen[str], label: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            log(f"{label}: {text}")


def run_and_monitor(label: str, command: list[str], *, progress_kind: str) -> None:
    log(f"Starting {label}: {' '.join(shlex.quote(part) for part in command)}")
    proc = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    reader = threading.Thread(target=stream_subprocess_output, args=(proc, label), daemon=True)
    reader.start()

    last_heartbeat = 0.0
    while True:
        now = time.time()
        if now - last_heartbeat >= MONITOR_INTERVAL_SECONDS:
            if progress_kind == "aggregate":
                done, details = count_aggregated_families()
                status = {
                    "phase": label,
                    "progress_kind": progress_kind,
                    "done": done,
                    "total": EXPECTED_FAMILIES,
                    "details": details,
                    "aggregation_output_root": str(AGG_OUTPUT_ROOT),
                    "evaluation_run_dir": str(EVAL_RUN_DIR),
                }
                log(f"{label} heartbeat: {done}/{EXPECTED_FAMILIES} family skills written; current={details}")
            elif progress_kind == "evaluate":
                done, last_tasks = count_evaluated_tasks()
                status = {
                    "phase": label,
                    "progress_kind": progress_kind,
                    "done": done,
                    "total": EXPECTED_TASKS,
                    "details": last_tasks,
                    "aggregation_output_root": str(AGG_OUTPUT_ROOT),
                    "evaluation_run_dir": str(EVAL_RUN_DIR),
                }
                log(f"{label} heartbeat: {done}/{EXPECTED_TASKS} task summaries written; latest={last_tasks}")
            else:
                raise ValueError(f"Unsupported progress kind: {progress_kind}")
            write_status(status)
            last_heartbeat = now

        rc = proc.poll()
        if rc is not None:
            reader.join(timeout=5)
            if rc != 0:
                raise RuntimeError(f"{label} failed with exit code {rc}")
            break
        time.sleep(POLL_SECONDS)

    if progress_kind == "aggregate":
        done, details = count_aggregated_families()
        log(f"{label} completed: {done}/{EXPECTED_FAMILIES} family skills written; final={details}")
    else:
        done, last_tasks = count_evaluated_tasks()
        log(f"{label} completed: {done}/{EXPECTED_TASKS} task summaries written; latest={last_tasks}")


def format_md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["-" * max(3, len(header)) for header in headers]) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, separator_line, *body_lines])


def build_756_section(new_row: FormalTestRow) -> str:
    training_rows = [
        ["`7.3.5`", "`gpt-5.4`", "全量 `248` 题 run 中已完成的同批 `140` 题", "`140`", "`107`", "`107 / 140 = 0.7643`"],
        ["`7.5`", "`SSSAI gpt-5.2`", "同一批 `140` 题正式 rerun", "`140`", "`94`", "`94 / 140 = 0.6714`"],
    ]

    formal_rows = [
        FormalTestRow("`7.3.5` baseline", "`gpt-5.4`", "`v5 codex`", 107, "同批 `140` 题", 59, 0.4214, "`7.3.5` 正式基线"),
        FormalTestRow("`7.3.5`", "`gpt-5.4`", "外部 `gpt-5.4 API` 聚合", 107, "同批 `140` 题", 54, 0.3857, "`v5_success107_shlab54api_fixed_20260405`"),
        new_row,
        FormalTestRow("`7.5`", "`gpt-5.2`", "`Codex`", 94, "同批 `140` 题", 62, 0.4429, "当前 `7.5` 正式最好分数"),
        FormalTestRow("`7.5`", "`gpt-5.2`", "外部 `gpt-5.4 API` 聚合", 94, "同批 `140` 题", 55, 0.3929, "去掉 Codex 变量后的 `5.4 API` 版"),
        FormalTestRow("`7.5`", "`gpt-5.2`", "真实 `gpt-5.2 API` 聚合", 94, "同批 `140` 题", 57, 0.4071, "去掉 Codex 变量后的 `5.2 API` 版"),
    ]

    auxiliary_rows = [
        ["`7.5` 初次误跑版", "`gpt-5.2`", "`Codex` 手工聚合", "数据集前 `140` 题", "`50`", "`0.3571`", "不是原训练那批 `140` 题"],
        ["`7.5` 缺失 `25` 题补测", "`gpt-5.2`", "`Codex` 手工聚合", "same140 缺失 `25` 题", "`13`", "`0.5200`", "只是为了和前 `115` 题合并回正式总表"],
    ]

    formal_rows_md = [
        [
            row.stage,
            row.training_model,
            row.aggregation_method,
            f"`{row.source_count}`",
            row.test_set,
            f"`{row.success_count}`",
            f"`{row.accuracy:.4f}`",
            row.note,
        ]
        for row in formal_rows
    ]

    ranking_rows_sorted = sorted(formal_rows, key=lambda row: (-row.accuracy, -row.success_count, row.stage, row.aggregation_method))
    ranking_rows_md: list[list[str]] = []
    for index, row in enumerate(ranking_rows_sorted, start=1):
        ranking_rows_md.append(
            [
                f"`{index}`",
                row.training_model,
                row.aggregation_method,
                f"`{row.success_count}`",
                f"`{row.accuracy:.4f}`",
            ]
        )

    section = f"""#### 7.5.6 训练 / 聚合 / 测试组合总表

为了避免 `7.5` 这一段后面越看越乱，这里把当前真正落盘过的组合一次列清楚。

先说最重要的结论：

- **训练结果一共 `2` 组**；
- **正式可同口径比较的测试结果一共 `6` 组**；
- 另外还有 **`2` 组辅助测试**，它们真实存在，但不应和正式同口径结果混在一起看。

问：当前两组训练结果分别是什么？

答：训练侧当前就是下面这两组正式结果。

{format_md_table(
    ["训练阶段", "训练模型", "训练 run / 口径", "完成题数", "成功保留 source skill", "训练 Accuracy"],
    training_rows,
)}

问：那正式可比较的测试，其实是哪 `6` 组？

答：如果只保留**同口径、可正式横向比较**的结果，就是下面这 `6` 组。

{format_md_table(
    ["测试阶段", "训练模型", "聚合方式", "聚合 source 数", "测试题集", "success_count", "Accuracy", "备注"],
    formal_rows_md,
)}

这 `6` 组如果按最终测试分数排序，就是：

{format_md_table(
    ["排名", "训练模型", "聚合方式", "success_count", "Accuracy"],
    ranking_rows_md,
)}

问：那你说的“辅助测试”是哪两组？为什么不算正式可比结果？

答：是下面这两组。它们是正式流程里的中间产物，但不该拿来和上面的 `6` 组并排当最终分数。

{format_md_table(
    ["辅助测试", "训练模型", "聚合方式", "测试题集", "success_count", "Accuracy", "为什么不算正式同口径"],
    auxiliary_rows,
)}

所以这里最后可以一句话记住：

- **训练只有 `2` 组**：`gpt-5.4` 训练、`gpt-5.2` 训练；
- **正式可比测试共有 `6` 组**：`7.3.5 baseline` + `7.3.5 gpt-5.4 API 聚合` + `7.3.5 gpt-5.2 API 聚合` + `7.5 Codex 聚合` + `7.5 gpt-5.4 API 聚合` + `7.5 gpt-5.2 API 聚合`；
- 如果把中间过程也算进去，`7.5` 额外还有 `2` 组辅助测试，但它们不进入最终正式对比表。
"""
    return section


def update_doc_with_new_result(*, success_count: int, accuracy: float) -> None:
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    new_row = FormalTestRow(
        stage="`7.3.5`",
        training_model="`gpt-5.4`",
        aggregation_method="真实 `gpt-5.2 API` 聚合",
        source_count=107,
        test_set="同批 `140` 题",
        success_count=success_count,
        accuracy=accuracy,
        note="`v5_success107_gpt52api_20260405_r1`",
    )
    replacement = build_756_section(new_row)
    pattern = re.compile(
        r"#### 7\.5\.6 训练 / 聚合 / 测试组合总表\n.*?(?=\n### 7\.6 本地 Gemma-4 替代训练侧 `gpt-5\.4`（待启动）)",
        re.S,
    )
    updated_text, count = pattern.subn(replacement.rstrip() + "\n", doc_text, count=1)
    if count != 1:
        raise RuntimeError("Failed to locate section 7.5.6 for replacement.")
    DOC_PATH.write_text(updated_text, encoding="utf-8")
    log("Updated 7.5.6 table section in experiment document.")


def main() -> None:
    log("==== Starting orchestrated run: 7.3.5 gpt-5.4 source -> gpt-5.2 API aggregate -> GPU0 same140 eval ====")
    write_status(
        {
            "phase": "boot",
            "aggregation_output_root": str(AGG_OUTPUT_ROOT),
            "evaluation_run_dir": str(EVAL_RUN_DIR),
            "task_file": str(TASK_FILE),
            "monitor_interval_seconds": MONITOR_INTERVAL_SECONDS,
        }
    )

    aggregate_cmd = [
        str(PYTHON_BIN),
        str(RUN_PIPELINE),
        "aggregate",
        "--config",
        str(AGG_CONFIG),
        "--input-run-dir",
        str(SOURCE_RUN_DIR),
        "--output-root",
        str(AGG_OUTPUT_ROOT),
    ]
    run_and_monitor("aggregate", aggregate_cmd, progress_kind="aggregate")

    evaluate_cmd = [
        str(PYTHON_BIN),
        str(RUN_PIPELINE),
        "evaluate",
        "--config",
        str(EVAL_CONFIG),
        "--skill-library-root",
        str(AGG_OUTPUT_ROOT),
        "--run-root",
        str(EVAL_RUN_ROOT),
        "--task-file",
        str(TASK_FILE),
        "--concurrency",
        "20",
        "--run-name",
        EVAL_RUN_NAME,
    ]
    run_and_monitor("evaluate", evaluate_cmd, progress_kind="evaluate")

    aggregation_summary = read_json(AGG_OUTPUT_ROOT / "aggregation_summary.json")
    evaluation_summary = read_json(EVAL_RUN_DIR / "evaluation_summary.json")
    success_count = int(evaluation_summary["success_count"])
    accuracy = float(evaluation_summary["avg_metrics"]["accuracy"])

    update_doc_with_new_result(success_count=success_count, accuracy=accuracy)

    orchestration_summary = {
        "aggregation_output_root": str(AGG_OUTPUT_ROOT),
        "aggregation_source_mode": aggregation_summary.get("source_mode", ""),
        "evaluation_run_dir": str(EVAL_RUN_DIR),
        "success_count": success_count,
        "accuracy": round(accuracy, 4),
        "doc_path": str(DOC_PATH),
    }
    ensure_parent(SUMMARY_JSON)
    SUMMARY_JSON.write_text(json.dumps(orchestration_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_status({"phase": "completed", **orchestration_summary})
    log(
        "Completed orchestrated run: "
        f"success_count={success_count}, accuracy={accuracy:.4f}, "
        f"doc_updated={DOC_PATH}"
    )


if __name__ == "__main__":
    main()
