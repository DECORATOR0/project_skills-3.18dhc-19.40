#!/usr/bin/env bash

set -euo pipefail

project_root="/data/xsy/project_skills-3.18dhc-19.40"
python_bin="/data/xsy/miniconda3/envs/earth-bench-skill-eval/bin/python"
config_path="${CONFIG_PATH:-$project_root/configs/system-train-xsy-gpt54-jh-v6-promptsnapshot-goldfix248.json}"
task_file="$project_root/data/task_sets/default_batch/train_all_30.txt"
bootstrap_snapshot="${BOOTSTRAP_SNAPSHOT-$project_root/tmp/v6_bootstrap_snapshot}"
runtime_root="$project_root/runs/temp/eo_runtime"
compat_root="$project_root/benchmark/out"
script_dir="$project_root/scripts"

run_name="${RUN_NAME:-$(date +%Y%m%d_%H%M%S)_train_batch30_v6prompt_jh_gpt54_c4_datafix_bridge}"
concurrency="${CONCURRENCY:-4}"
bridge_interval="${BRIDGE_INTERVAL_SECONDS:-1}"
bridge_log="$project_root/runs/_launch_logs/${run_name}_bridge.log"
pid_file="$project_root/runs/_launch_logs/${run_name}.pid"

# Prevent stale shell overrides from silently rerouting actor/critic away from the JSON-configured Shanghai AI Lab endpoint.
unset NLRL_LLM_MODEL NLRL_LLM_BASE_URL NLRL_LLM_API_KEY NLRL_LLM_API_MODE
unset NLRL_ACTOR_MODEL NLRL_ACTOR_BASE_URL NLRL_ACTOR_API_KEY NLRL_ACTOR_API_MODE
unset NLRL_CRITIC_MODEL NLRL_CRITIC_BASE_URL NLRL_CRITIC_API_KEY NLRL_CRITIC_API_MODE
unset NLRL_EXECUTOR_MODEL NLRL_EXECUTOR_BASE_URL NLRL_EXECUTOR_API_KEY NLRL_EXECUTOR_API_MODE

export NLRL_LLM_MAX_CONCURRENT_REQUESTS="${NLRL_LLM_MAX_CONCURRENT_REQUESTS:-4}"
export NLRL_ACTOR_STREAM="${NLRL_ACTOR_STREAM:-1}"
export NLRL_ACTOR_TIMEOUT_SECONDS="${NLRL_ACTOR_TIMEOUT_SECONDS:-420}"
export PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$(dirname "$bridge_log")"
cd "$project_root"
printf '%s\n' "$$" > "$pid_file"

cleanup_targets() {
  local task_id question_dir module_dir

  while IFS= read -r task_id; do
    task_id="$(printf '%s' "$task_id" | tr -d '[:space:]')"
    [[ -z "$task_id" ]] && continue
    question_dir="question${task_id}"

    rm -rf "$compat_root/$question_dir"

    while IFS= read -r -d '' module_dir; do
      rm -rf "$module_dir/$question_dir"
    done < <(find "$runtime_root" -mindepth 1 -maxdepth 1 -type d -print0)
  done < "$task_file"
}

bridge_pid=""
stop_bridge() {
  if [[ -n "$bridge_pid" ]] && kill -0 "$bridge_pid" 2>/dev/null; then
    kill "$bridge_pid" 2>/dev/null || true
    wait "$bridge_pid" 2>/dev/null || true
  fi
}

trap stop_bridge EXIT INT TERM

printf '%s run_start run_name=%s\n' "$(date --iso-8601=seconds)" "$run_name"
printf '%s wrapper_pid=%s pid_file=%s\n' "$(date --iso-8601=seconds)" "$$" "$pid_file"
printf '%s config=%s\n' "$(date --iso-8601=seconds)" "$config_path"
printf '%s prompt_root=%s\n' "$(date --iso-8601=seconds)" "$project_root/dhc-4.11prompt迭代/V6/prompt_root_snapshot"
printf '%s task_file=%s\n' "$(date --iso-8601=seconds)" "$task_file"
if [[ -n "$bootstrap_snapshot" ]]; then
  printf '%s bootstrap_snapshot=%s\n' "$(date --iso-8601=seconds)" "$bootstrap_snapshot"
else
  printf '%s bootstrap_snapshot=%s\n' "$(date --iso-8601=seconds)" "<llm-single-skill-bootstrap>"
fi
printf '%s concurrency=%s llm_cap=%s actor_stream=%s actor_timeout=%s\n' \
  "$(date --iso-8601=seconds)" "$concurrency" "$NLRL_LLM_MAX_CONCURRENT_REQUESTS" \
  "$NLRL_ACTOR_STREAM" "$NLRL_ACTOR_TIMEOUT_SECONDS"

cleanup_targets
printf '%s cleanup_complete\n' "$(date --iso-8601=seconds)"

"$script_dir/benchmark_out_bridge.sh" \
  --runtime-root "$runtime_root" \
  --compat-root "$compat_root" \
  --tasks-file "$task_file" \
  --interval "$bridge_interval" \
  > "$bridge_log" 2>&1 &
bridge_pid="$!"

printf '%s bridge_started pid=%s bridge_log=%s\n' "$(date --iso-8601=seconds)" "$bridge_pid" "$bridge_log"

cmd=(
  "$python_bin" -m nlrl_skills.cli
  --config "$config_path"
  train-task-local-parallel
  --concurrency "$concurrency"
  --task-file "$task_file"
  --run-name "$run_name"
)
if [[ -n "$bootstrap_snapshot" ]]; then
  cmd+=(--bootstrap-snapshot "$bootstrap_snapshot")
fi

set +e
"${cmd[@]}"
status="$?"
set -e

printf '%s train_exit status=%s\n' "$(date --iso-8601=seconds)" "$status"
exit "$status"
