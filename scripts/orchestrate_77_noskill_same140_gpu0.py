#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path("/data/xsy/project_skills-3.18dhc-19.40").resolve()
PYTHON_BIN = Path("/data/xsy/miniconda3/envs/earth-bench-skill-eval/bin/python")
RUN_PIPELINE = PROJECT_ROOT / "scripts" / "run_pipeline.py"

TASK_FILE = PROJECT_ROOT / "data" / "task_sets" / "formal_same140_20260404" / "task_ids.txt"
SKILL_LIBRARY_ROOT = (
    PROJECT_ROOT
    / "runs"
    / "train_task_local_parallel_completed140_sssai_gpt52_localqwen_gpu1_c20_rerun_20260404"
    / "aggregated_skill_library_6_gpt52_rerun_20260404"
)
EVAL_CONFIG = PROJECT_ROOT / "configs" / "system.eval.local_qwen3_8b_stream140_gpu0_isolated.json"
RUN_ROOT = PROJECT_ROOT / "runs_gpu0"
RUN_NAME = "eval_noskill_executor_gpt52success94_localqwen3_gpu0_same140_c20_20260406_r3"
RUN_DIR = RUN_ROOT / RUN_NAME

MONITOR_LOG = RUN_ROOT / "_orchestration_noskill_same140_gpu0_20260406_r3.log"
STATUS_JSON = RUN_ROOT / "_orchestration_noskill_same140_gpu0_20260406_r3.status.json"
SUMMARY_JSON = RUN_ROOT / "_orchestration_noskill_same140_gpu0_20260406_r3.summary.json"

MONITOR_INTERVAL_SECONDS = 600
POLL_SECONDS = 30
EXPECTED_TASKS = 140


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def log(message: str) -> None:
    ensure_parent(MONITOR_LOG)
    with MONITOR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp()}] {message}\n")


def write_json(path: Path, payload: dict) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def count_evaluated_tasks() -> tuple[int, list[str]]:
    task_paths = sorted(path.parent.name for path in RUN_DIR.glob("task_*/evaluation_summary.json"))
    return len(task_paths), task_paths[-5:]


def stream_subprocess_output(proc: subprocess.Popen[str], label: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            log(f"{label}: {text}")


def build_command() -> list[str]:
    return [
        str(PYTHON_BIN),
        str(RUN_PIPELINE),
        "evaluate",
        "--config",
        str(EVAL_CONFIG),
        "--skill-library-root",
        str(SKILL_LIBRARY_ROOT),
        "--task-file",
        str(TASK_FILE),
        "--concurrency",
        "20",
        "--evaluation-mode",
        "no-skill-executor",
        "--run-name",
        RUN_NAME,
    ]


def run_and_monitor(command: list[str]) -> None:
    env = os.environ.copy()
    env["HTTP_PROXY"] = ""
    env["HTTPS_PROXY"] = ""
    env["ALL_PROXY"] = ""

    log(f"Starting no-skill same140 evaluation: {' '.join(shlex.quote(part) for part in command)}")
    proc = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    reader = threading.Thread(target=stream_subprocess_output, args=(proc, "evaluate"), daemon=True)
    reader.start()

    last_heartbeat = 0.0
    while True:
        now = time.time()
        if now - last_heartbeat >= MONITOR_INTERVAL_SECONDS:
            done, last_tasks = count_evaluated_tasks()
            status = {
                "phase": "evaluate",
                "evaluation_mode": "no-skill-executor",
                "done": done,
                "total": EXPECTED_TASKS,
                "latest_tasks": last_tasks,
                "run_dir": str(RUN_DIR),
                "task_file": str(TASK_FILE),
                "skill_library_root": str(SKILL_LIBRARY_ROOT),
            }
            write_json(STATUS_JSON, status)
            log(f"Heartbeat: {done}/{EXPECTED_TASKS} task summaries written; latest={last_tasks}")
            last_heartbeat = now

        rc = proc.poll()
        if rc is not None:
            reader.join(timeout=5)
            if rc != 0:
                raise RuntimeError(f"Evaluation failed with exit code {rc}")
            break
        time.sleep(POLL_SECONDS)

    done, last_tasks = count_evaluated_tasks()
    log(f"Evaluation completed: {done}/{EXPECTED_TASKS} task summaries written; latest={last_tasks}")


def write_summary() -> None:
    evaluation_summary_path = RUN_DIR / "evaluation_summary.json"
    evaluation_summary = json.loads(evaluation_summary_path.read_text(encoding="utf-8"))
    payload = {
        "run_dir": str(RUN_DIR),
        "evaluation_mode": evaluation_summary.get("evaluation_mode", "no-skill-executor"),
        "task_count": evaluation_summary.get("task_count", 0),
        "success_count": evaluation_summary.get("success_count", 0),
        "avg_metrics": evaluation_summary.get("avg_metrics", {}),
        "named_avg_metrics": evaluation_summary.get("named_avg_metrics", {}),
        "task_file": str(TASK_FILE),
        "skill_library_root": str(SKILL_LIBRARY_ROOT),
        "config": str(EVAL_CONFIG),
    }
    write_json(SUMMARY_JSON, payload)
    log(f"Summary written to {SUMMARY_JSON}")


def main() -> None:
    command = build_command()
    run_and_monitor(command)
    write_summary()
    print(f"run_dir: {RUN_DIR}")
    print(f"summary_json: {SUMMARY_JSON}")


if __name__ == "__main__":
    main()
