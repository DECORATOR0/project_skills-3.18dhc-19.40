#!/usr/bin/env bash

set -euo pipefail

runtime_root=""
compat_root=""
tasks_file=""
interval_seconds="1"
run_once="0"

usage() {
  cat <<'EOF'
Usage:
  benchmark_out_bridge.sh --runtime-root PATH --compat-root PATH [options]

Options:
  --tasks-file PATH     Limit syncing to question ids listed in the file.
  --interval SECONDS    Sleep interval between sync cycles. Default: 1
  --once                Run a single sync cycle and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-root)
      runtime_root="${2:-}"
      shift 2
      ;;
    --compat-root)
      compat_root="${2:-}"
      shift 2
      ;;
    --tasks-file)
      tasks_file="${2:-}"
      shift 2
      ;;
    --interval)
      interval_seconds="${2:-}"
      shift 2
      ;;
    --once)
      run_once="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$runtime_root" || -z "$compat_root" ]]; then
  usage >&2
  exit 1
fi

if [[ ! -d "$runtime_root" ]]; then
  echo "runtime root not found: $runtime_root" >&2
  exit 1
fi

mkdir -p "$compat_root"

declare -A target_questions=()
if [[ -n "$tasks_file" ]]; then
  while IFS= read -r raw_id; do
    task_id="$(printf '%s' "$raw_id" | tr -d '[:space:]')"
    [[ -z "$task_id" ]] && continue
    [[ "$task_id" =~ ^[0-9]+$ ]] || continue
    target_questions["question${task_id}"]=1
  done < "$tasks_file"
fi

should_sync_rel() {
  local rel="$1"

  if [[ ${#target_questions[@]} -eq 0 ]]; then
    return 0
  fi

  local question_dir
  for question_dir in "${!target_questions[@]}"; do
    case "$rel" in
      "${question_dir}"/*|*/"${question_dir}"/*)
        return 0
        ;;
    esac
  done

  return 1
}

sync_once() {
  local synced=0
  local module_dir src rel dst current_target

  while IFS= read -r -d '' module_dir; do
    while IFS= read -r -d '' src; do
      rel="${src#"$module_dir"/}"
      should_sync_rel "$rel" || continue

      dst="$compat_root/$rel"
      mkdir -p "$(dirname "$dst")"

      if [[ -L "$dst" ]]; then
        current_target="$(readlink -f "$dst" || true)"
        if [[ "$current_target" == "$src" ]]; then
          continue
        fi
      fi

      ln -sfn "$src" "$dst"
      synced=$((synced + 1))
      printf '%s synced %s -> %s\n' "$(date --iso-8601=seconds)" "$src" "$dst"
    done < <(find "$module_dir" \( -type f -o -type l \) -print0)
  done < <(find "$runtime_root" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

  printf '%s sync_cycle_complete synced=%s\n' "$(date --iso-8601=seconds)" "$synced"
}

if [[ "$run_once" == "1" ]]; then
  sync_once
  exit 0
fi

printf '%s bridge_start runtime_root=%s compat_root=%s interval=%s tasks_file=%s\n' \
  "$(date --iso-8601=seconds)" "$runtime_root" "$compat_root" "$interval_seconds" "${tasks_file:-<all>}"

while true; do
  sync_once
  sleep "$interval_seconds"
done
