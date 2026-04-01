# RL Training Data Format

## Goal
This project normalizes heterogeneous supervision sources into one unified format for skill-oriented natural-language RL.

Supported source types:
- `A`: human completed a repeatable task and the full behavior trace is available
- `B`: human guided an agent through a first-time task, including failures and corrections
- `C`: benchmark task with a gold solving trajectory

## Normalized File
The normalized dataset is stored as a single `question.json` with this top-level structure:

```json
{
  "dataset_name": "string",
  "dataset_version": "string",
  "task_count": 0,
  "task_schema_version": "nlrl-skill-training-v1",
  "tasks": []
}
```

## Normalized Task Schema

```json
{
  "task_id": "earth-bench-c-1",
  "source_type": "C",
  "prompt": "task instruction",
  "choices": ["A ...", "B ...", "C ...", "D ..."],
  "gold_answer": "A",
  "data_dir": "benchmark/data/question1",
  "file_list": ["file_a.tif", "file_b.tif"],
  "gold_tool_names": ["get_filelist", "compute_tvdi", "calc_batch_image_mean", "compute_linear_trend"],
  "gold_trajectory": [
    {
      "role": "assistant",
      "thought": "...",
      "tool_calls": [...]
    },
    {
      "role": "tool",
      "name": "get_filelist",
      "content": {...}
    }
  ],
  "metadata": {
    "original_question_id": "1",
    "evaluation_type": "Autonomous Planning",
    "source_file": "benchmark/question.json"
  }
}
```

## Field Semantics
- `task_id`: stable normalized identifier used by the RL framework
- `source_type`: one of `A`, `B`, `C`
- `prompt`: the task instruction exposed to the environment
- `choices`: optional multiple-choice candidates
- `gold_answer`: gold final answer label or canonical answer string
- `data_dir`: logical task data directory
- `file_list`: discovered input files, if known from the source trace
- `gold_tool_names`: ordered gold tool list extracted from the gold trajectory
- `gold_trajectory`: full gold or human trace used by critic and actor
- `metadata`: source-specific identifiers and provenance

## How To Map The Three Source Types

### Type A
Use when a successful human task trace already exists.
- `prompt`: original user request
- `gold_answer`: final successful answer
- `gold_trajectory`: the human trace
- `metadata.trace_origin`: `human_direct`

### Type B
Use when the trace contains failures and human corrections.
- `prompt`: original task request
- `gold_answer`: corrected final answer
- `gold_trajectory`: store the full corrected sequence, not only the first failed attempt
- `metadata.corrections_present`: `true`
- optionally keep failure snippets inside `metadata.failure_spans`

### Type C
Use benchmark tasks with a gold trajectory.
- `prompt`: benchmark task text
- `gold_answer`: benchmark label or gold string
- `gold_trajectory`: benchmark gold dialogs/tool calls
- `metadata.original_question_id`: original benchmark id

## Why This Format Fits The RL Loop
The framework needs the same fields regardless of source:
- Env needs `prompt`, `choices`, `data_dir`, and optionally `file_list`
- Critic needs `gold_trajectory` and `gold_tool_names`
- Actor needs `gold_trajectory` to synthesize or revise a reusable skill
- The experience buffer stores compact failure summaries orthogonal to the source type

## Current EO Conversion
The current EO dataset in this workspace is treated as `Type C`.

Conversion rule:
- read `benchmark/question.json`
- keep only `evaluation.type == "Autonomous Planning"`
- use the corresponding task text as `prompt`
- extract `gold_answer` from `gt_answer.whitelist`
- extract `gold_tool_names` from `dialogs[].tool_calls[].function.name`
- preserve the full `dialogs` as `gold_trajectory`
- recover `data_dir` from any evaluation block that contains `data`

The generated normalized file is:
- `data/converted/earth_bench_skill_rl/question.json`
