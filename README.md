# Earth-Bench Single-Skill Batch RL

Natural-language reinforcement learning framework that trains and evaluates a **single agent skill** on Earth-Bench tasks using **phase-based progressive disclosure**.

## Architecture

The system follows a strict single-skill batch RL loop with progressive disclosure:

1. **Bootstrap**: Generate exactly one phase-based skill from the batch's questions and gold tool-chain truth. The skill defines execution phases (`## Phase: NAME`) that guide the executor step by step.
2. **Env Execution (Progressive Disclosure)**: Run all questions **concurrently** through a Qwen3-8B executor. The executor receives only one phase at a time — phase content is revealed progressively as the executor transitions between phases using `<NEXT>PHASE</NEXT>` tags.
3. **Batch Critic**: Evaluate all traces against the skill, producing natural-language reward across five dimensions: Task Alignment, Phase Structure, Progressive Disclosure Compliance, Efficiency & Hallucination, Script & Reference Usage.
4. **Actor Modification**: Revise the single skill based on the critic reward and history poll. The actor outputs complete skill files including SKILL.md, optional scripts/, and references/.
5. **Iterate**: Repeat steps 2-4 for `k` iterations (default 5), then move to the next batch.

## Progressive Disclosure Model

Following the [Anthropic Agent Skills specification](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) and the [Agent Skills open standard](https://agentskills.io/specification):

| Level | When Loaded | Content |
|-------|-------------|---------|
| Level 1: Metadata | Always (at startup) | `name` and `description` from YAML frontmatter |
| Level 2: Phase Instructions | One phase at a time | Current phase content from SKILL.md body |
| Level 3: Resources | On demand via tool calls | `scripts/`, `references/`, `assets/` files |

### Phase-Based Execution Flow

```
Environment starts executor in Phase: INIT
    │
    ├─ Executor receives: task info + INIT phase content + tool list
    │
    ├─ Executor outputs one of:
    │   ├─ <CALL>tool_name</CALL><ARGS>{...}</ARGS>  → tool executed, result fed back
    │   ├─ <NEXT>PHASE_NAME</NEXT>                   → next phase content revealed
    │   └─ <ANSWER>B</ANSWER>                         → final answer captured, loop ends
    │
    ├─ On phase transition: env reveals ONLY the new phase's content
    │   (previous phases are NOT re-injected — each phase is self-contained)
    │
    └─ Loop continues until <ANSWER> or max_steps reached
```

### Tag-Based Executor Protocol

The executor uses XML-like tags instead of JSON for output (optimized for 8B models):

```
<THOUGHT>reasoning grounded in task and current phase</THOUGHT>
<CALL>tool_name</CALL>
<ARGS>{"param1": "value1"}</ARGS>
```

```
<THOUGHT>ready to transition</THOUGHT>
<NEXT>PROCESS_SPECTRUM</NEXT>
```

```
<THOUGHT>evidence supports answer B</THOUGHT>
<ANSWER>B</ANSWER>
```

## Skill Format

Each skill follows the Anthropic Agent Skills directory pattern with phase-based instructions:

```
earth-bench-skill/
├── SKILL.md          # Required: YAML frontmatter + phase definitions
├── scripts/          # Optional: Python helper scripts (deterministic computation)
├── references/       # Optional: detailed documentation
└── assets/           # Optional: templates, resources
```

### SKILL.md Structure

```markdown
---
name: earth-bench-batch-skill
description: Phase-based executor guidance for Earth-Bench remote sensing tasks.
allowed-tools:
  - list_dir
  - read_file
  - run_python_script
  - get_data_info
  - ...
metadata:
  version: "1.0"
---

## Phase: INIT

**Goal**: Explore the task data directory.

**Instructions**:
1. Use `list_dir` to check the data directory.
2. Note file types and count.

**Available actions**:
- <CALL>list_dir</CALL><ARGS>{"path": "DATA_DIR"}</ARGS>
- <NEXT>IDENTIFY</NEXT> when files are explored

## Phase: IDENTIFY

**Goal**: Determine task modality from file patterns.

**Available actions**:
- <NEXT>PROCESS_SPECTRUM</NEXT> for spectrum tasks
- <NEXT>PROCESS_PRODUCTS</NEXT> for band product tasks
- <NEXT>PROCESS_RGB</NEXT> for RGB tasks

## Phase: PROCESS_SPECTRUM

**Goal**: Process spectrum data using EO tools.
[Detailed tool call instructions with examples]

## Phase: CONCLUDE

**Goal**: Review evidence and give final answer.

You MUST output: <ANSWER>A</ANSWER> (or B, C, D)
Choose from the task's available choices.
```

YAML frontmatter fields:
- `name` (required): max 64 chars, lowercase letters/numbers/hyphens
- `description` (required): max 1024 chars, non-empty
- `allowed-tools` (optional): list of permitted tool names
- `compatibility` (optional): max 500 chars
- `metadata` (optional): arbitrary key-value mapping

## Directory Structure

```
configs/              Main configuration (configs/system.json)
nlrl_skills/          NLRL training framework
  cli.py              CLI entry point
  task_local_trainer.py   Single-skill batch RL trainer
  environment.py      Phase-based progressive disclosure environment
  agent_loop.py       Tag-based executor loop (PhaseExecutorAgent)
  critic.py           Batch-level critic (5 reward dimensions)
  actor.py            Single-skill actor (modify only)
  evaluation.py       Metric computation (6 metrics)
  skills.py           Skill filesystem ops + phase parsing
  config.py           System configuration
  schemas.py          Data classes (SkillPhase, PhaseTransition, etc.)
  data.py             Dataset loading and conversion
  task_buckets.py     Task modality classification
  llm.py              OpenAI-compatible LLM client
  tools.py            Toolbox (EO tools + builtins)
  prompting.py        Prompt template rendering
  utils.py            Utilities
agent/skill_eval/     Evaluation entry point
  run_skill_executor.py   Unified evaluation CLI
agent/tools/          Earth-Bench tool implementations
prompts/              Prompt templates
  executor_system.md      Executor system prompt (phase-based)
  executor_user.md        Executor user prompt template
  tool_agent_protocol.md  Tag-based action protocol (<CALL>, <NEXT>, <ANSWER>)
  critic_system.md        Batch critic system prompt (5 dimensions)
  critic_user.md          Batch critic user prompt
  actor_system.md         Actor system prompt (skill authoring spec)
  actor_modify_skill.md   Actor modify-skill prompt
  bootstrap_batch_skill_system.md   Bootstrap system prompt (phase-based)
  bootstrap_batch_skill_library.md  Bootstrap user prompt (single skill)
data/converted/       Normalized training data
benchmark/            Benchmark source data and data root pointer
runs/                 Training artifacts (auto-created)
```

The skill is stored per-run under `runs/<run>/batch_state/skill_library/`.

## Evaluation Metrics

Six metrics are computed for both training and testing:

| Name | Key | Formula |
|------|-----|---------|
| Tool-Any-Order (TAO) | `tool_any_order` | |gold ∩ pred| / |gold| |
| Tool-In-Order (TIO) | `tool_in_order` | LCS(pred, gold) / |gold| |
| Tool-Exact-Match (TEM) | `tool_exact_match` | Longest common prefix / |gold| |
| Parameters | `parameter_accuracy` | Matched-prefix with structural equality / |gold| |
| Efficiency | `efficiency` | |pred| / |gold| |
| Accuracy | `accuracy` | 1.0 if predicted label == gold label, else 0.0 |

## Critic Reward Dimensions

The critic evaluates five dimensions:

| Dimension | Focus |
|-----------|-------|
| Task Alignment | Did the executor reach the correct answer? Why not? |
| Phase Structure | Are phases adequate for all task types? Dead-ends? Missing branches? |
| Progressive Disclosure | Is each phase self-contained? Is CONCLUDE properly structured? |
| Efficiency & Hallucination | Wasted steps, fabricated values, failed tool loops? |
| Script & Reference Usage | Should computation be delegated to scripts? Are references used? |

## Running Environment

```bash
conda activate dch_skill
```

Requirements:
- Run all commands from the repo root.
- Module entrypoints use `python -m ...`.
- Training and evaluation call `http://35.220.164.252:3888/v1` directly.

## Common Commands

### 1. Convert Earth-Bench Data

```bash
python -m nlrl_skills.cli --config configs/system.json convert-earth-bench \
  --src benchmark/question.json \
  --dst data/converted/earth_bench_skill_rl/question.json
```

### 2. Export Default 30-Question Task Set

```bash
python -m nlrl_skills.cli --config configs/system.json sample-task-set \
  --output-dir data/task_sets/default_batch
```

### 3. Train (Single-Skill Batch RL)

Default (auto-selects 30 tasks: 10 spectrum + 10 products + 10 rgb):

```bash
python -m nlrl_skills.cli --config configs/system.json train-task-local-parallel \
  --run-name train_batch_30
```

With explicit task file:

```bash
python -m nlrl_skills.cli --config configs/system.json train-task-local-parallel \
  --task-file data/task_sets/default_batch/train_all_30.txt \
  --run-name train_batch_30_custom
```

Training artifacts are written to `runs/<run_name>/`:
- `run_summary.json`: final summary with metrics and history poll
- `history_poll.json`: accumulated problems from each iteration
- `iteration_XX/`: per-iteration traces, critic reward, actor decision
- `iteration_XX/active_skill.json`: skill header + phase list for this iteration
- `batch_state/skill_library/`: the trained skill (SKILL.md + scripts/ + references/)

### 4. Evaluate with Trained Skill

```bash
python -m agent.skill_eval.run_skill_executor \
  --config configs/system.json \
  --skill-dir runs/train_batch_30/batch_state/skill_library \
  --all \
  --output agent/skill_eval/execution_results
```

Single question:

```bash
python -m agent.skill_eval.run_skill_executor \
  --config configs/system.json \
  --skill-dir runs/train_batch_30/batch_state/skill_library \
  --question 226
```

### 5. Inspect Current Skill Library

```bash
python -m nlrl_skills.cli --config configs/system.json inspect-skills
```

## Configuration

Key settings in `configs/system.json`:

| Field | Default | Description |
|-------|---------|-------------|
| `runtime.iterations_per_batch` | 5 | Number of RL iterations per batch |
| `runtime.max_executor_steps` | 20 | Max steps per task (tool calls + phase transitions) |
| `executor.model` | qwen3-8b | Executor LLM (8B ReAct agent with tag protocol) |
| `actor.model` | gpt-5.4 | Actor LLM (skill modification) |
| `critic.model` | gpt-5.4 | Critic LLM (batch evaluation) |

LLM 调用不限制上下文 tokens（`max_tokens` 和 `max_context_chars` 均未设置，不对输入/输出做截断）。

## LLM Retry 与错误处理

所有 LLM 调用（executor / critic / actor / bootstrap）使用统一的 retry 策略：

- **任何异常**均触发重试（不区分 HTTP 状态码或网络错误类型）。
- 报错后间隔 **6 秒**重新尝试，最多重试 **5 次**（首次 + 5 次重试 = 6 次尝试）。
- 每次重试均输出 WARNING 日志（含错误摘要）到终端和日志文件。
- 5 次重试全部失败后：
  - 输出 ERROR 日志，包含**最后一次报错的完整内容**。
  - **不执行任何兜底/降级逻辑**。

失败后的行为取决于角色：

| 角色 | 失败行为 |
|------|---------|
| **Executor** | 跳过该题（记录 `task_success=False`），继续执行 batch 中的其他题目 |
| **Critic** | 终止并跳过当前 iteration（标记 `skipped=True`），进入下一轮 iteration |
| **Actor** | 终止并跳过当前 iteration（标记 `skipped=True`），进入下一轮 iteration |
| **Bootstrap** | 训练直接终止（异常上抛），无确定性回退技能 |

可通过环境变量覆盖默认值：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `NLRL_LLM_MAX_RETRIES` | `5` | 最大重试次数 |
| `NLRL_LLM_RETRY_DELAY_SECONDS` | `6` | 重试间隔（秒） |

## 并发执行

训练和测试中的 batch 任务执行均为**全并发**（并发数 = batch 中的题目数量）。
由于所有 LLM 调用均通过远程 API，不受本地 CPU/GPU 限制，直接以 batch 大小作为并发上限。

每个并发 worker 拥有独立的 `SkillEnvironment`（含独立的 `httpx.Client` 和 `Toolbox`），
共享同一个 `EOToolRuntime`（EO 工具模块只加载一次）。

## 进度与日志

- **终端进度**：使用 `tqdm` 在终端显示实时进度条。
  - 训练：外层 RL iteration 进度条 + 每轮内 executor batch 进度条（含 done/success 统计）。
  - 评估：全部题目的完成进度条。
- **日志落盘**：所有日志同时输出到终端（`StreamHandler`）和文件（`FileHandler`）。
  - 训练日志：`runs/<run_name>/run.log`
  - 评估日志：`<output_dir>/<timestamp>/run.log`
  - 事件记录：`runs/<run_name>/events.jsonl`（结构化 JSONL，每个 task/iteration 事件一行）
  - LLM 调用记录：每次 LLM 请求/响应以 JSON 文件落盘到对应 step 目录
