"""
Shared configuration for the skill-based Earth-Bench evaluation flow.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_benchmark_root() -> Path:
    env_root = os.environ.get("EARTH_BENCH_ROOT", "").strip()
    candidates = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(
        [
            PROJECT_ROOT / "benchmark",
            Path("/data/xsy/skill-pool/benchmark/benchmark"),
            Path("/data/xsy/skill-pool/benchmark"),
        ]
    )
    for candidate in candidates:
        if (candidate / "question.json").exists():
            return candidate
        nested = candidate / "benchmark"
        if (nested / "question.json").exists():
            return nested
    return PROJECT_ROOT / "benchmark"


BENCHMARK_ROOT = _resolve_benchmark_root()
BENCHMARK_PATH = BENCHMARK_ROOT / "question.json"
QUESTION_SYNC_PATH = PROJECT_ROOT / "question_sync.json"
TOOLS_DIR = PROJECT_ROOT / "agent" / "tools"
TOOL_SOURCE_DIR = TOOLS_DIR
TOOL_FILES = ["Index.py", "Inversion.py", "Perception.py", "Analysis.py", "Statistics.py"]
DATA_DIR_CANDIDATES = [
    BENCHMARK_ROOT / "data",
    BENCHMARK_ROOT / "Earth-Bench",
    PROJECT_ROOT / "benchmark" / "data",
    PROJECT_ROOT / "benchmark" / "Earth-Bench",
]
MAX_SHORTLISTED_TOOLS = 45
LLM_API_MAX_CONCURRENCY = int(os.environ.get("SKILL_EVAL_LLM_API_MAX_CONCURRENCY", "20"))

# Skill-aware planner configuration.
# This planning stage keeps the original single-agent shape: shortlist -> one
# planner LLM call -> full tool sequence.
SKILL_PLANNER_MODEL_NAME = "qwen3-8b"
SKILL_PLANNER_PRIMARY_BASE_URL = "http://35.220.164.252:3888/v1"
SKILL_PLANNER_BACKUP_BASE_URL = "http://35.220.164.252:3888/v1"
SKILL_PLANNER_API_KEY = "sk-fJAaw6TWC8JjH7mzHDqY78lcpr6VdvZhkXVD6isc3oLi7fGZ"
SKILL_PLANNER_MAX_TOKENS = 8192
SKILL_PLANNER_MAX_RETRIES = 3
SKILL_PLANNER_REQUEST_TIMEOUT = 120
SKILL_PLANNER_TEMPERATURE = 0.5

# Direct single-executor configuration for the built-in 6-skill line.
DIRECT_EXECUTOR_MODEL_NAME = SKILL_PLANNER_MODEL_NAME
DIRECT_EXECUTOR_MAX_TOKENS = 8192
DIRECT_EXECUTOR_MAX_STEPS = 50

HTTP_PROXY = os.environ.get("HTTP_PROXY", "http://127.0.0.1:7890")
HTTPS_PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890")

SKILL_EVAL_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = PROJECT_ROOT / ".claude" / "skills"

# Skill parameter model configuration.
# This model is used only for stepwise tool-argument generation inside the skill flow.

PARAMETER_MODEL_NAME = "qwen3-8b"
PARAMETER_MODEL_BASE_URL = "http://35.220.164.252:3888/v1"
PARAMETER_MODEL_BACKUP_URL = "http://35.220.164.252:3888/v1"
PARAMETER_MODEL_API_KEY = "sk-fJAaw6TWC8JjH7mzHDqY78lcpr6VdvZhkXVD6isc3oLi7fGZ"
PARAMETER_MODEL_CONTEXT_WINDOW = 8192
PARAMETER_MODEL_ENABLE_THINKING = False
PARAMETER_MAX_RETRIES = 3
PARAMETER_REQUEST_TIMEOUT = 120

# PARAMETER_MODEL_NAME = "Qwen/Qwen3-8B"
# PARAMETER_MODEL_BASE_URL = "http://35.220.164.252:3888/v1/"
# PARAMETER_MODEL_BACKUP_URL = "http://34.13.73.248:3888/v1"
# # Local endpoint does not require authentication.
# PARAMETER_MODEL_API_KEY = "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ"
# # Qwen3-8B-RL is served with a 32k context window on the local endpoint.
# PARAMETER_MODEL_CONTEXT_WINDOW = 32768
# PARAMETER_MODEL_ENABLE_THINKING = True
# PARAMETER_MAX_RETRIES = 3
# PARAMETER_REQUEST_TIMEOUT = 120

# PARAMETER_MODEL_NAME = "gpt-5.4"
# PARAMETER_MODEL_BASE_URL = "http://35.220.164.252:3888/v1/"
# PARAMETER_MODEL_BACKUP_URL = "http://34.13.73.248:3888/v1"
# # Local endpoint does not require authentication.
# PARAMETER_MODEL_API_KEY = "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ"
# # Qwen3-8B-RL is served with a 32k context window on the local endpoint.
# PARAMETER_MAX_RETRIES = 3
# PARAMETER_REQUEST_TIMEOUT = 120


# Final 4-choice answer selector model configuration.
# This model is used only after skill execution has finished.
ANSWER_SELECTOR_MODEL_NAME = "qwen3-8b"
ANSWER_SELECTOR_PRIMARY_BASE_URL = "http://35.220.164.252:3888/v1"
ANSWER_SELECTOR_BACKUP_BASE_URL = "http://35.220.164.252:3888/v1"
ANSWER_SELECTOR_API_KEY = "sk-fJAaw6TWC8JjH7mzHDqY78lcpr6VdvZhkXVD6isc3oLi7fGZ"
ANSWER_SELECTOR_MAX_TOKENS = 2048
ANSWER_SELECTOR_MAX_RETRIES = 3
ANSWER_SELECTOR_REQUEST_TIMEOUT = 120

DEFAULT_TEMP_DIR = PROJECT_ROOT / "benchmark" / "out" / "skill_eval"
DEFAULT_TEMP_DIR.mkdir(parents=True, exist_ok=True)

MAX_WORKER_STEPS = 12
MAX_CONTEXT_CHARS = 24000

WORKER_SYSTEM_PROMPT = """
You are a worker agent in a geoscientist multi-agent system.
Your objective is to advance the task step by step using the available tools. Note that the provided tools may not be able to complete the entire task; your goal is to use them effectively to make progress wherever possible.

 Guidelines:
 1. Analyze the current context.
 2. Identify the most effective action.
 3. Call the appropriate tool with well-formed arguments.
 4. Prioritize actions that produce new, useful information.

 When uncertain, prefer calling a tool rather than guessing. Avoid redundant tool calls and ensure each action meaningfully moves the task forward.
""".strip()

ANSWER_SELECTION_NOTE = (
    "The final 4-choice answer selection is a separate post-skill step. "
    "Do not bake answer-choice guessing into the skill itself."
)

PROXY_HINT = {
    "HTTP_PROXY": HTTP_PROXY,
    "HTTPS_PROXY": HTTPS_PROXY,
}
