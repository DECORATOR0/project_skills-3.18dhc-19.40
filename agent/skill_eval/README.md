# Skill-Based Earth-Bench Evaluation

This directory contains the current `skill_eval` pipeline for Earth-Bench `Autonomous Planning` questions.

The current design keeps the planning flow aligned with your original single-agent idea:

1. build a benchmark-aware shortlist
2. send `question + data_dir + file list + shortlisted tools` to one planner LLM call
3. let the LLM output the full `tool_sequence`
4. use skills as planning priors, prompt refinements, routed tool focus, and later-stage execution constraints

The main difference from the old generic single-agent baseline is that planning is now **skill-aware**, while parameter generation and final answer selection are explicit later stages.

## Current Skills

The current runtime routes questions into these `6` skills:

- `earth-spectrum-thermal-retrieval`
- `earth-spectrum-drought-stress`
- `earth-product-timeseries`
- `earth-product-derived-index-change`
- `earth-product-raster-arithmetic`
- `earth-rgb-perception-change`

The human-readable skill definitions live in `.claude/skills/`, but the runtime source of truth is:

- `agent/skill_eval/skill_router.py`
- `agent/skill_eval/skill_llm_planner.py`
- `agent/skill_eval/parameter_worker.py`
- `agent/skill_eval/staged_runner.py`
- `agent/skill_eval/executor.py`

## Runtime Layout

Main scripts:

- `agent/skill_eval/run_skill_benchmark.py`
  - build inspection/debug manifests only
- `agent/skill_eval/run_skill_plan_stage.py`
  - staged pipeline, planning only
- `agent/skill_eval/run_skill_parameter_stage.py`
  - staged pipeline, parameter generation + tool execution
- `agent/skill_eval/run_skill_answer_stage.py`
  - staged pipeline, final 4-choice answer selection
- `agent/skill_eval/run_skill_executor.py`
  - one-shot end-to-end execution
- `agent/skill_eval/run_skill_plan_eval.py`
  - plan-only evaluation against gold tool trajectories

Core implementation files:

- `config.py`
- `skill_router.py`
- `skill_llm_planner.py`
- `skill_planner_client.py`
- `parameter_worker.py`
- `tool_router.py`
- `tool_rendering.py`
- `prompt_builder.py`
- `staged_runner.py`
- `executor.py`
- `runtime.py`
- `benchmark_loader.py`
- `evaluator.py`

## Planning Logic

The planning stage currently follows this flow:

1. infer a benchmark-aware question profile
2. build a shortlist from the parsed tool catalog
3. route the question to one skill
4. add skill-specific planning guidance to the planner prompt
5. call one planner LLM to produce the full `tool_sequence`
6. sanitize and repair the returned sequence against the shortlist

The implementation is in `agent/skill_eval/skill_llm_planner.py`.

Planning is **not** a pure hard-coded template path anymore. The current planning sources are:

- `skill-llm-json`
  - primary path, planner asked for JSON tool sequence
- `skill-llm-tagged-fallback`
  - secondary path, planner asked for tagged-text tool sequence
- `skill-template-fallback`
  - deterministic template fallback only if both LLM planning paths fail

These values are written into plan outputs and logs.

## Model Configuration

`skill_eval` now keeps all three model roles in `agent/skill_eval/config.py`.

### Planner model

Used only for full tool-sequence planning:

- `SKILL_PLANNER_MODEL_NAME`
- `SKILL_PLANNER_PRIMARY_BASE_URL`
- `SKILL_PLANNER_BACKUP_BASE_URL`
- `SKILL_PLANNER_API_KEY`
- `SKILL_PLANNER_MAX_RETRIES`
- `SKILL_PLANNER_REQUEST_TIMEOUT`
- `SKILL_PLANNER_TEMPERATURE`

The dedicated planner client is:

- `agent/skill_eval/skill_planner_client.py`

### Parameter model

Used only for stepwise argument generation:

- `PARAMETER_MODEL_NAME`
- `PARAMETER_MODEL_BASE_URL`
- `PARAMETER_MODEL_BACKUP_URL`
- `PARAMETER_MODEL_API_KEY`
- `PARAMETER_MODEL_CONTEXT_WINDOW`
- `PARAMETER_MODEL_ENABLE_THINKING`
- `PARAMETER_MAX_RETRIES`
- `PARAMETER_REQUEST_TIMEOUT`

Important current behavior:

- if `PARAMETER_MODEL_CONTEXT_WINDOW == -1`, parameter calls do not pass `max_tokens`
- otherwise the parameter worker passes `max_tokens = PARAMETER_MODEL_CONTEXT_WINDOW`

The parameter worker is:

- `agent/skill_eval/parameter_worker.py`

`skill_eval` parameter stage now prefers tool-call style outputs, and only uses
text parsing as a fallback.

### Final answer selector model

Used only after execution has finished:

- `ANSWER_SELECTOR_MODEL_NAME`
- `ANSWER_SELECTOR_PRIMARY_BASE_URL`
- `ANSWER_SELECTOR_BACKUP_BASE_URL`
- `ANSWER_SELECTOR_API_KEY`
- `ANSWER_SELECTOR_MAX_RETRIES`
- `ANSWER_SELECTOR_REQUEST_TIMEOUT`

The answer-selector client is:

- `agent/skill_eval/llm_client.py`

## Benchmark Filtering

Only `Autonomous Planning` questions are evaluated.

This is handled by `agent/skill_eval/benchmark_loader.py`, which:

- loads `benchmark/question.json`
- keeps only `evaluation.type == "Autonomous Planning"` questions
- extracts gold tool names and gold tool calls from `dialogs[].tool_calls[].function`
- resolves the benchmark data directory

## Manifest Builder

Use this when you want a per-question manifest for inspection or debugging without executing the benchmark:

```powershell
conda activate earth-bench-skill-eval
python -m agent.skill_eval.run_skill_benchmark --all --output agent/skill_eval/results
```

Examples:

```powershell
python -m agent.skill_eval.run_skill_benchmark --question 1 --output agent/skill_eval/results
python -m agent.skill_eval.run_skill_benchmark --start 1 --end 20 --output agent/skill_eval/results
python -m agent.skill_eval.run_skill_benchmark --all --include-first-decision --output agent/skill_eval/results
```

Each manifest item includes:

- question text and choices
- routed skill
- routed shortlist and rendered tool text
- planner system prompt and planner user prompt
- worker prompt scaffold
- final answer-selector messages
- MCP server launch commands

## Staged Pipeline

The staged route is useful when you want better observability or want to inspect intermediate artifacts between phases.

### Stage 1: planning only

```powershell
python -m agent.skill_eval.run_skill_plan_stage --all --concurrency 24 --output agent/skill_eval/plan_stage_results -v
```

This stage:

- runs only the skill-aware planner
- records planner prompts and raw outputs
- saves per-question planning artifacts
- computes:
  - `TAO`
  - `TIO`
  - `TEM`
  - `Efficiency`

Outputs:

- batch:
  - `run.log`
  - `plan_records.json`
  - `plan_summary.json`
- per question:
  - `question_<id>/plan_record.json`
  - `question_<id>/plan_log.json`
  - `question_<id>/planner_trace.json`

`planner_trace.json` includes:

- planner system prompt
- planner user prompt
- planner raw output
- planner fallback reason
- final planned tools

### Stage 2: parameter generation + tool execution

```powershell
python -m agent.skill_eval.run_skill_parameter_stage --input <plan_stage_run_dir> --all --concurrency 24 --output agent/skill_eval/parameter_stage_results -v
```

This stage:

- loads Stage 1 plan artifacts
- reuses the saved shortlisted tool set
- iterates through the planned tool sequence
- for each step:
  - asks the parameter-selection LLM for arguments when needed
  - or uses deterministic fallback arguments for stable execution
  - executes the tool
  - appends the new observation for the next step
- computes:
  - `Parameter Accuracy`

Outputs:

- batch:
  - `run.log`
  - `parameter_records.json`
  - `parameter_summary.json`
- per question:
  - `question_<id>/parameter_record.json`
  - `question_<id>/parameter_log.json`
  - `question_<id>/parameter_trace.json`

`parameter_trace.json` includes:

- each parameter prompt
- returned arguments
- rationale
- raw response
- cleaned response
- model name
- fallback information
- runtime initialization status

### Stage 3: final 4-choice answer selection

```powershell
python -m agent.skill_eval.run_skill_answer_stage --input <parameter_stage_run_dir> --all --concurrency 24 --output agent/skill_eval/answer_stage_results -v
```

This stage:

- loads Stage 2 parameter/execution artifacts
- builds the final execution summary
- calls the final answer-selector LLM
- falls back to deterministic random choice if needed
- computes:
  - `Accuracy`

Outputs:

- batch:
  - `run.log`
  - `answer_records.json`
  - `answer_summary.json`
- per question:
  - `question_<id>/answer_record.json`
  - `question_<id>/answer_log.json`
  - `question_<id>/answer_trace.json`

`answer_trace.json` includes:

- answer-selector messages
- raw answer-selector output
- final selected option
- fallback information

## One-Shot Executor

If you want the entire benchmark flow in one command:

```powershell
conda activate earth-bench-skill-eval
python -m agent.skill_eval.run_skill_executor --all --concurrency 24 --output agent/skill_eval/execution_results -v
```

If you want the routed 6-skill family to be consumed by one direct tool-using executor instead of the staged `planner -> parameter_worker -> answer_selector` chain:

```powershell
python -m agent.skill_eval.run_skill_executor --all --pipeline-mode direct-executor --concurrency 24 --output agent/skill_eval/execution_results -v
```

Single-question example:

```powershell
python -m agent.skill_eval.run_skill_executor --question 226 --output agent/skill_eval/execution_results -v
```

The one-shot executor:

- plans
- generates parameters
- executes tools
- selects the final answer
- computes the full metric set in one run

`run_skill_executor` now supports two pipeline modes:

- `staged`
  - the original `planner -> parameter_worker -> executor -> answer_selector` path
- `direct-executor`
  - keeps the same code-routed 6-skill family and shortlist, but lets one executor model consume the routed skill directly and choose tools step by step

Outputs:

- batch:
  - `run.log`
  - `records.json`
  - `batch_summary.json`
- per question:
  - `question_<id>/execution_record.json`
  - `question_<id>/execution_log.json`
  - `question_<id>/llm_trace.json`

`llm_trace.json` includes:

- planner prompts and raw output
- parameter-worker prompts and raw outputs
- answer-selector messages and raw output
- planner fallback reason
- fatal error if execution failed before later stages

## Plan-Only Evaluation

Use this when you want to evaluate planning quality against the gold tool trajectory without executing tools or doing final answer selection:

```powershell
conda activate earth-bench-skill-eval
python -m agent.skill_eval.run_skill_plan_eval --all --output agent/skill_eval/plan_eval_results -v
```

Examples:

```powershell
python -m agent.skill_eval.run_skill_plan_eval --question 226 --output agent/skill_eval/plan_eval_results -v
python -m agent.skill_eval.run_skill_plan_eval --start 1 --end 50 --output agent/skill_eval/plan_eval_results -v
```

This path evaluates:

- `TAO`
- `TIO`
- `TEM`
- predicted tool count vs. gold count

It also records:

- planner system prompt
- planner user prompt
- planner raw output
- planner fallback reason
- planning source

## Logging And Observability

All runners write a `run.log` file and also print useful progress lines to the console.

Current behavior:

- normal mode:
  - concise stage-level progress
- `-v` mode:
  - richer pipeline-stage debug output
  - low-level `httpx/httpcore/openai` noise is reduced so console logs stay readable
  - raw LLM prompts and responses still go to JSON trace files rather than flooding the terminal

The one-shot executor also guarantees that even early failures still produce per-question files:

- `execution_record.json`
- `execution_log.json`
- `llm_trace.json`

## Metrics

Staged metrics:

- Stage 1:
  - `TAO`
  - `TIO`
  - `TEM`
  - `Efficiency`
- Stage 2:
  - `Parameter Accuracy`
- Stage 3:
  - `Accuracy`

One-shot executor metrics:

- `Accuracy`
- `Efficiency`
- `Tools-Any-Order (TAO)`
- `Tools-In-Order (TIO)`
- `Tool-Exact-Match (TEM)`
- `Parameter Accuracy`

`Parameter Accuracy` uses prefix matching on both tool name and arguments.

## Worker Prompt Design

The parameter worker uses the fixed system prompt from `agent/skill_eval/config.py`.

Its user prompt contains:

- current context or the original question
- `Relevant datas are stored at {data path}`
- rendered tool descriptions and argument schemas

This preserves your required design that tool arguments are decided one step at a time rather than in one shot.

Relevant files:

- `agent/skill_eval/prompt_builder.py`
- `agent/skill_eval/tool_rendering.py`
- `agent/skill_eval/parameter_worker.py`

## Environment

Recommended environment file:

- `environment.skill-eval.yml`

Typical setup:

```powershell
conda env create -f environment.skill-eval.yml
conda activate earth-bench-skill-eval
```

If the environment already exists:

```powershell
conda env update -f environment.skill-eval.yml --prune
conda activate earth-bench-skill-eval
```

## Practical Runtime Caveats

### Missing dependencies

The most important missing runtime dependency previously observed on this machine was:

- `fastmcp`

If it is missing, Stage 2 and the one-shot executor can fail during tool-runtime initialization, even when Stage 1 planning is fine.

### RGB runtime note

`agent/tools/Perception.py` currently depends on precomputed outputs from `benchmark/model_results.csv` rather than a live perception backend.

### Tool execution style

The benchmark tools are authored as MCP tools in `agent/tools/`, but the local executor currently imports and calls the Python functions directly through `runtime.py` for reliability and easier logging.

The helper code in `agent/skill_eval/mcp_servers.py` still generates MCP launch specs for inspection/manifests.

## Recommended Full Benchmark Flow

For debugging and better observability, the staged route is recommended:

1. Run Stage 1 planning
2. Inspect `plan_summary.json` and representative `planner_trace.json`
3. Run Stage 2 parameter generation + execution
4. Inspect `parameter_summary.json` and representative `parameter_trace.json`
5. Run Stage 3 final answer selection
6. Inspect `answer_summary.json`

If you only need a single command and can accept mixed-stage artifacts in one run directory, use the one-shot executor.

## Command Examples

Single question, staged:

```powershell
python -m agent.skill_eval.run_skill_plan_stage --question 1 --output agent/skill_eval/plan_stage_results -v
python -m agent.skill_eval.run_skill_parameter_stage --input agent/skill_eval/plan_stage_results/<timestamp> --question 1 --output agent/skill_eval/parameter_stage_results -v
python -m agent.skill_eval.run_skill_answer_stage --input agent/skill_eval/parameter_stage_results/<timestamp> --question 1 --output agent/skill_eval/answer_stage_results -v
```

All questions, staged:

```powershell
python -m agent.skill_eval.run_skill_plan_stage --all --concurrency 24 --output agent/skill_eval/plan_stage_results -v
python -m agent.skill_eval.run_skill_parameter_stage --input agent/skill_eval/plan_stage_results/<timestamp> --all --concurrency 24 --output agent/skill_eval/parameter_stage_results -v
python -m agent.skill_eval.run_skill_answer_stage --input agent/skill_eval/parameter_stage_results/<timestamp> --all --concurrency 24 --output agent/skill_eval/answer_stage_results -v
```

All questions, one-shot:

```powershell
python -m agent.skill_eval.run_skill_executor --all --concurrency 24 --output agent/skill_eval/execution_results -v
python -m agent.skill_eval.run_skill_executor --all --pipeline-mode direct-executor --concurrency 24 --output agent/skill_eval/execution_results -v
```

Manifest-only debug run:

```powershell
python -m agent.skill_eval.run_skill_benchmark --all --output agent/skill_eval/results
```
