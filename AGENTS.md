# AGENTS.md

## Working Rules

- Run commands from the repo root: `/data/xsy/project_skills-3.18dhc-19.40`.
- Use the eval environment interpreter: `/data/xsy/miniconda3/envs/earth-bench-skill-eval/bin/python`.
- When using module entrypoints, keep the cwd at repo root and run `python -m ...`.
- For this Linux project, training, evaluation, and `agent.skill_eval` should call `http://35.220.164.252:3888/v1` directly. Do not rely on `HTTP_PROXY` or `HTTPS_PROXY`.

## GDAL / osgeo Guardrail

- This repo contains a local `osgeo` shim only as a fallback for environments without GDAL.
- If you see `GDAL runtime is not available in this environment`, first verify:
  - the active interpreter is the eval environment
  - `from osgeo import gdal` resolves to site-packages instead of the repo shim
  - the command is being launched from the repo root

## Canonical Entrypoints

- `python -m nlrl_skills.cli --config configs/system.json ...`
- `python -m agent.skill_eval.run_skill_executor ...`
- `python -m agent.skill_eval.run_skill_plan_stage ...`
