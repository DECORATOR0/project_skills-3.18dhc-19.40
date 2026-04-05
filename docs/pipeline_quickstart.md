# Pipeline Quickstart

## What This Adds

Use `scripts/run_pipeline.py` as the single entry point for:
- data checks
- task-set export
- classic shared-library training
- task-local parallel training
- aggregation into the final 6-skill library
- evaluation
- one-click local train -> aggregate -> evaluate
- minimal end-to-end smoke

If your environment is already activated, `python3` is fine. Otherwise prefer:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py list-configs
```

## Data Layout

The expected benchmark layout is:

```text
benchmark/
  question.json
  data/
    question1/
    question2/
    ...
    question248/
```

Current code accepts either:
- a real `benchmark/data/` directory
- a symlinked `benchmark/data/` directory

In this repo, `benchmark/data` currently points to a shared benchmark checkout. That is acceptable because the framework resolves task data through relative paths such as `benchmark/data/question37`.

The real source of truth is:
- `benchmark/question.json` contains valid `evaluation[*].data` paths
- `data/converted/earth_bench_skill_rl/question.json` contains valid `tasks[*].data_dir` paths

Check it directly with:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py check-data --config system
```

## Config Selection

The runner accepts either a direct config path or a short alias from `configs/`.

Examples:
- `--config system`
- `--config system.train_local_actor_critic_sssai_gpt52`
- `--config configs/system.eval.local_qwen3_8b_stream140_gpu0_isolated.json`

List available aliases:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py list-configs
```

## Common Commands

Export the default task files:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py sample --config system --seed 20260403 --quota-scale 1
```

Run a single debug episode:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py train-debug --config system --task-id 1 --run-name debug_q1 --reset-skill-library --reset-experience-buffer
```

Run classic shared-library training:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py train-classic --config system --count 5 --run-name train_first5 --reset-skill-library --reset-experience-buffer
```

Run task-local parallel training:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py train-local --config system.train_local_actor_critic_sssai_gpt52 --task-file data/task_sets/task_local_parallel_seed_20260403_x1/train_all_30.txt --concurrency 20 --run-name train_local_c20
```

Aggregate a task-local run:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py aggregate --config system.train_local_actor_critic_sssai_gpt52 --input-run-dir runs/train_local_c20
```

Evaluate a skill library:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py evaluate --config system --skill-library-root runs/train_local_c20/aggregated_skill_library --count 20 --concurrency 20 --run-name eval_after_aggregate
```

Run the full task-local pipeline in one command:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py full-local --config system.train_local_actor_critic_sssai_gpt52 --task-file data/task_sets/task_local_parallel_seed_20260403_x1/train_all_30.txt --concurrency 20 --eval-concurrency 20 --run-name local_full_c20
```

Run a minimal end-to-end smoke:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py smoke --config system.train_local_actor_critic_sssai_gpt52
```

## Useful Overrides

Every mode supports these common overrides:
- `--run-root`
- `--skill-library-root`
- `--experience-buffer-path`
- `--dataset-path`
- `--converted-dataset-path`
- `--docs-root`
- `--max-iterations`
- `--max-executor-steps`
- `--max-actor-steps`
- `--max-context-chars`
- `--skill-count-limit`
- `--skill-match-threshold`

Examples:

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py train-local --config system.train_local_actor_critic_sssai_gpt52 --count 1 --concurrency 1 --run-root /tmp/nlrl_runs --max-iterations 1 --max-executor-steps 2 --max-actor-steps 1
```

```bash
conda run -n earth-bench-skill-eval python scripts/run_pipeline.py evaluate --config system --skill-library-root /tmp/aggregated_skill_library --task-ids 1 2 3 --concurrency 3
```
