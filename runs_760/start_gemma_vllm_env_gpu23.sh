#!/bin/bash
set -euo pipefail

ROOT="/data/xsy/project_skills-3.18dhc-19.40"
ENV_PY="/data/xsy/miniconda3/envs/vllm_gemma_env/bin/python"
ENV_PIP="/data/xsy/miniconda3/envs/vllm_gemma_env/bin/pip"
WHEEL_PATH="${ROOT}/runs_760/cache/vllm-0.19.0-cp38-abi3-manylinux_2_31_x86_64.whl"
LOG_DIR="${ROOT}/runs_760/logs"
INSTALL_LOG="${LOG_DIR}/vllm_gemma_env_install.log"
WAIT_LOG="${LOG_DIR}/vllm_gemma_gpu23_wait.log"
SERVE_LOG="${LOG_DIR}/vllm_gemma4_gpu23_8200.log"

mkdir -p "${LOG_DIR}"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

echo "[$(timestamp)] start gemma launcher" | tee -a "${WAIT_LOG}"
echo "[$(timestamp)] install vllm stack in dedicated env" | tee -a "${WAIT_LOG}"

{
  echo "[$(timestamp)] step1: reinstall vllm wheel without dependency churn"
  if [ -f "${WHEEL_PATH}" ]; then
    "${ENV_PIP}" install --no-deps --force-reinstall "${WHEEL_PATH}"
  else
    "${ENV_PIP}" install --no-deps --force-reinstall vllm==0.19.0
  fi

  echo "[$(timestamp)] step2: upgrade only the compatibility-critical packages"
  "${ENV_PIP}" install --upgrade \
    torch==2.10.0 \
    torchaudio==2.10.0 \
    torchvision==0.25.0 \
    transformers==4.57.6 \
    aiohttp==3.13.5 \
    protobuf==7.34.1 \
    compressed-tensors==0.14.0.1 \
    depyf==0.20.0 \
    llguidance==1.3.0 \
    opencv-python-headless==4.13.0.92 \
    xgrammar==0.1.33 \
    ijson

  echo "[$(timestamp)] step3: capture package versions"
  "${ENV_PY}" - <<'PY'
import aiohttp
import torch
import transformers
import vllm
from google.protobuf import __version__ as protobuf_version
print("vllm", vllm.__version__)
print("torch", torch.__version__)
print("transformers", transformers.__version__)
print("aiohttp", aiohttp.__version__)
print("protobuf", protobuf_version)
PY
} >>"${INSTALL_LOG}" 2>&1

echo "[$(timestamp)] install step done" | tee -a "${WAIT_LOG}"
"${ENV_PY}" - <<'PY' >>"${WAIT_LOG}" 2>&1
import vllm, torch, transformers
print("vllm", vllm.__version__)
print("torch", torch.__version__)
print("transformers", transformers.__version__)
PY

GPU23_UUIDS="$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | awk -F', ' '$1==2 || $1==3 {print $2}')"

echo "[$(timestamp)] waiting for GPU2/GPU3 to become idle" | tee -a "${WAIT_LOG}"
while true; do
  ACTIVE_LINES="$(nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader 2>/dev/null || true)"
  BLOCKERS=""
  while IFS= read -r uuid; do
    [ -z "${uuid}" ] && continue
    MATCHED="$(printf '%s\n' "${ACTIVE_LINES}" | grep "${uuid}" || true)"
    if [ -n "${MATCHED}" ]; then
      BLOCKERS="${BLOCKERS}${MATCHED}"$'\n'
    fi
  done <<< "${GPU23_UUIDS}"

  if [ -z "${BLOCKERS}" ]; then
    echo "[$(timestamp)] GPU2/GPU3 are free, starting Gemma service" | tee -a "${WAIT_LOG}"
    break
  fi

  {
    echo "[$(timestamp)] GPU2/GPU3 still occupied:"
    printf '%s\n' "${BLOCKERS}"
  } >>"${WAIT_LOG}"
  sleep 30
done

exec env CUDA_VISIBLE_DEVICES=2,3 "${ENV_PY}" -u -m vllm.entrypoints.openai.api_server \
  --model /data/xsy/codes/checkpoints/gemma-4-31B-it \
  --served-model-name Gemma-4-31B-it-local \
  --host 0.0.0.0 \
  --port 8200 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.95 \
  --dtype bfloat16 \
  --trust-remote-code \
  --enable-prefix-caching \
  --max-model-len 65536 \
  --max-num-seqs 2 \
  --swap-space 16 \
  --cpu-offload-gb 8 \
  >>"${SERVE_LOG}" 2>&1
