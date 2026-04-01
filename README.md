# NL-RL Skill Training Framework

This project implements a natural-language reinforcement learning framework that trains an agent to build and refine an Agent Skills library from benchmark tasks.

The framework is centered on:
- `actor`
- `critic`
- `env`
- `skills library`
- `NL-Experience Buffer`

It is currently configured for the EO task setting in this workspace and uses OpenAI-compatible endpoints.

## What Was Built

### Core framework
- independent LLM configs for `actor`, `critic`, `router`, and `executor`
- prompt files separated into `prompts/`
- skill discovery and parsing based on `SKILL.md`
- skill library editing with create / merge / modify actions
- NL-Experience Buffer persisted as JSONL
- EO tool bridge that directly loads `agent/tools/*.py`
- Earth-Bench-style evaluation metrics rewritten into the new project
- structured per-run logging with request/response traces and per-iteration state snapshots

### Data layer
- normalized training-data schema for source types `A`, `B`, and `C`
- converter for the current EO `benchmark/question.json`
- converted dataset written to:
  - `data/converted/earth_bench_skill_rl/question.json`

### Current trained skills
The current skill library now includes:
- `skill_library/ndvi-lst-tvdi-annual-trend/`
- `skill_library/ndvi-lst-tvdi-spike-count/`
- `skill_library/ndvi-lst-tvdi-single-date-threshold-exceedance/`
- `skill_library/ndvi-lst-tvdi-multidate-area-exceedance-count/`

## Directory Overview

```text
configs/
  system.json

docs/
  agent_skill_definition.md
  training_data_format.md

nlrl_skills/
  actor.py
  agent_loop.py
  cli.py
  config.py
  critic.py
  data.py
  environment.py
  evaluation.py
  llm.py
  prompting.py
  router.py
  schemas.py
  skills.py
  tools.py
  trainer.py
  utils.py

prompts/
  actor_action_selection.md
  actor_create_skill.md
  actor_merge_skill.md
  actor_modify_skill.md
  actor_system.md
  critic_system.md
  critic_user.md
  executor_system.md
  executor_user.md
  router_system.md
  router_user.md
  tool_agent_protocol.md

skill_library/
  ndvi-lst-tvdi-annual-trend/
  ndvi-lst-tvdi-spike-count/
  ndvi-lst-tvdi-single-date-threshold-exceedance/
  ndvi-lst-tvdi-multidate-area-exceedance-count/

runtime_state/
  experience_buffer.jsonl

runs/
  debug_q1/
  env_probe_2/
  env_probe_3/
```

## LLM Roles

Configured in `configs/system.json`.

- `actor`
  - model: `gpt-5.4`
  - base url: `http://35.220.164.252:3888/v1`
- `critic`
  - model: `gpt-5.4`
  - base url: `http://35.220.164.252:3888/v1`
- `router`
  - model: `gpt-5.4`
  - base url: `http://35.220.164.252:3888/v1`
- `executor`
  - model: `Qwen/Qwen3-8B`
  - base url: `http://35.220.164.252:3888/v1`

All four roles are configured independently so they can be swapped later.

### `max_tokens` rule
- `actor` and `critic` do not send a `max_tokens` field.
- `router` sends `max_tokens` only if it is explicitly set in `configs/system.json`.
- `executor` sends `max_tokens` only if it is explicitly set in `configs/system.json`; the current config sets it to `32768`.

### Retry policy
Every LLM call retries up to 5 times with a 6-second interval for transient request/network failures, including common status-code failures such as `400`, `429`, `500`, and `502`.

## Skill Lifecycle In This Framework

1. Router scores every skill header independently.
2. If no skill crosses the threshold, execution stops and the critic sees a no-skill state.
3. If one skill is selected, the executor activates its `SKILL.md` and can use its bundled scripts/resources.
4. Critic emits a natural-language reward plus structured action guidance.
5. Actor chooses one first-layer action:
   - `create_skill`
   - `merge_skills`
   - `modify_skill`
6. Actor writes skill files into `skill_library/`.
7. Experience summaries are appended to `runtime_state/experience_buffer.jsonl`.

## EO Evaluation

The old `agent/skill_eval` logic was not reused directly. Instead, this project rewrites the needed evaluation logic into:
- `nlrl_skills/evaluation.py`

Implemented metrics:
- `accuracy`
- `efficiency`
- `tool_any_order`
- `tool_in_order`
- `tool_exact_match`
- `parameter_accuracy`

The EO tools themselves are still imported from:
- `agent/tools/*.py`

## Data Conversion

Convert the current EO benchmark:

```powershell
conda run -n earth-bench-skill-eval python -m nlrl_skills.cli --config "configs/system.json" convert-earth-bench --src "benchmark/question.json" --dst "data/converted/earth_bench_skill_rl/question.json"
```

The current converted file already exists at:
- `data/converted/earth_bench_skill_rl/question.json`

## Running A Debug Episode

Run the full RL loop on one task:

```powershell
conda run -n earth-bench-skill-eval python -m nlrl_skills.cli --config "configs/system.json" debug-single-task --task-id 1 --run-name "debug_q1" --reset-skill-library
```

Inspect current skill headers:

```powershell
conda run -n earth-bench-skill-eval python -m nlrl_skills.cli --config "configs/system.json" inspect-skills
```

Train on multiple tasks continuously:

```powershell
conda run -n earth-bench-skill-eval python -m nlrl_skills.cli --config "configs/system.json" train-tasks --count 5 --start-index 0 --run-name "train_first5_k10" --reset-skill-library --reset-experience-buffer
```

Evaluate separately after training:

```powershell
conda run -n earth-bench-skill-eval python -m nlrl_skills.cli --config "configs/system.json" evaluate-tasks --count 5 --start-index 0 --run-name "eval_first5_after_training"
```

## Logging

Each run creates a dedicated folder under `runs/`.

Per run:
- `config_snapshot.json`
- `task.json`
- `run_summary.json`

Per iteration:
- `skill_headers_before.json`
- `skill_headers_after.json`
- `iteration_summary.json`
- `iteration_failure.json` when a stage crashes

Per component:
- `env/state.json`
- `critic/reward.json`
- `actor/actor_decision.json`
- request/response JSON files for each LLM call

The executor additionally stores one request/response pair per tool step.

## Debug Findings From This Session

### What worked
- the end-to-end loop ran
- the critic produced usable NL reward
- the actor created a new EO skill from the gold trajectory
- the actor later revised that skill after execution feedback
- the router correctly selected the generated skill
- the framework now resolves skill-relative paths like `scripts/...` correctly
- the framework now retries LLM requests on transient rate limits
- the trainer now records iteration failures instead of crashing without artifacts

### Additional fixes made after data materialization
- `allowed-tools` YAML lists are now parsed correctly
- `allowed-tools` are enforced at execution time, not just shown in prompts
- executor prompt size was reduced by replacing full file lists with a preview plus count
- training and evaluation are now separate CLI flows
- 5-sample continuous training and a separate 5-sample evaluation flow were run and logged

## Notes On Prompt Editing

All prompts are plain markdown files in `prompts/`.

The most useful files to edit are:
- `prompts/router_system.md`
- `prompts/executor_system.md`
- `prompts/critic_system.md`
- `prompts/actor_create_skill.md`
- `prompts/actor_modify_skill.md`

## Dependencies

Minimal local requirements are listed in `requirements.txt`:
- `openai`
- `PyYAML`

The EO runtime additionally depends on the existing `earth-bench-skill-eval` environment, which already contains libraries like:
- `fastmcp`
- `rasterio`
- `numpy`
- `scipy`

## Current Status

Implemented and debugged:
- framework skeleton
- data conversion
- prompt externalization
- logging
- skill creation and revision
- EO evaluation rewrite
- skill-relative script path handling

Not yet resolved:
- `Qwen/Qwen3-8B` still struggles on long EO trajectories and can hit provider TPM limits during evaluation
- some trained skills still need more tightening around exact batch argument formatting for EO tools

## Recommended Next Step

Use the current logged results as the baseline, then iterate on:
- tighter skill call templates for batch EO tools
- stronger executor-side examples for list-valued arguments
- reduced-token router/executor prompts to avoid TPM spikes on long file lists
