#!/usr/bin/env bash
set -Eeuo pipefail

state_file=/state/current-model.json
status_file=/state/llama-status.json
child_pid=""
stopping=0

write_status() {
  local state="$1" message="$2" model="${3:-}"
  local tmp="${status_file}.tmp.$$"
  jq -n \
    --arg state "$state" \
    --arg message "$message" \
    --arg model "$model" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{state:$state,message:$message,model:$model,updated_at:$updated_at}' >"$tmp"
  mv -f "$tmp" "$status_file"
}

forward_signal() {
  stopping=1
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}
trap forward_signal TERM INT

state_fingerprint() {
  if [[ -f "$state_file" ]]; then
    sha256sum "$state_file" 2>/dev/null || true
  fi
}

while [[ "$stopping" -eq 0 ]]; do
  if [[ ! -s "$state_file" ]]; then
    write_status "WAITING" "No model selected. Run: ./llmctl download OWNER/REPO && ./llmctl use MODEL"
    sleep 5
    continue
  fi

  relative_model="$(jq -er '.primary_file | strings | select(length > 0)' "$state_file" 2>/dev/null || true)"
  model_id="$(jq -er '.model_id // .primary_file' "$state_file" 2>/dev/null || true)"
  if [[ -z "$relative_model" || "$relative_model" == /* || "$relative_model" == *".."* ]]; then
    write_status "ERROR" "Selected-model state is invalid; run ./llmctl use MODEL again"
    sleep 5
    continue
  fi

  model_path="/models/$relative_model"
  if [[ ! -f "$model_path" ]]; then
    write_status "ERROR" "Selected model file is missing: $relative_model" "$model_id"
    sleep 5
    continue
  fi

  args=(
    --model "$model_path"
    --host "${LLAMA_HOST:-0.0.0.0}"
    --port "${LLAMA_INTERNAL_PORT:-8080}"
    --ctx-size "${LLAMA_CONTEXT_SIZE:-32768}"
    --alias "${LLAMA_MODEL_ALIAS:-local-model}"
  )
  [[ -n "${LLAMA_GPU_LAYERS:-}" ]] && args+=(--n-gpu-layers "$LLAMA_GPU_LAYERS")
  [[ -n "${LLAMA_THREADS:-}" ]] && args+=(--threads "$LLAMA_THREADS")
  [[ -n "${LLAMA_BATCH_SIZE:-}" ]] && args+=(--batch-size "$LLAMA_BATCH_SIZE")
  [[ -n "${LLAMA_UBATCH_SIZE:-}" ]] && args+=(--ubatch-size "$LLAMA_UBATCH_SIZE")
  [[ -n "${LLAMA_FLASH_ATTN:-}" ]] && args+=(--flash-attn "$LLAMA_FLASH_ATTN")
  [[ -n "${LLAMA_PARALLEL:-}" ]] && args+=(--parallel "$LLAMA_PARALLEL")
  [[ "${LLAMA_JINJA:-true}" == "true" ]] && args+=(--jinja)

  if [[ -n "${LLAMA_EXTRA_ARGS:-}" ]]; then
    # This is intentionally parsed as shell-like words without evaluation.
    read -r -a extra <<<"$LLAMA_EXTRA_ARGS"
    args+=("${extra[@]}")
  fi

  fingerprint="$(state_fingerprint)"
  write_status "STARTING" "llama-server is loading the selected model" "$model_id"
  echo "Starting llama-server with model: $relative_model"
  /app/llama-server "${args[@]}" &
  child_pid=$!

  # Promote STARTING to RUNNING only after the real endpoint responds.
  (
    for _ in {1..1200}; do
      if curl -fsS "http://127.0.0.1:${LLAMA_INTERNAL_PORT:-8080}/health" >/dev/null 2>&1; then
        write_status "RUNNING" "llama-server is ready" "$model_id"
        exit 0
      fi
      kill -0 "$child_pid" 2>/dev/null || exit 0
      sleep 1
    done
  ) &
  watcher_pid=$!

  set +e
  wait "$child_pid"
  exit_code=$?
  set -e
  kill "$watcher_pid" 2>/dev/null || true
  wait "$watcher_pid" 2>/dev/null || true
  child_pid=""

  [[ "$stopping" -eq 1 ]] && exit 0

  write_status "ERROR" "llama-server exited with code $exit_code; inspect ./llmctl logs llama-cpp" "$model_id"
  echo "llama-server exited with code $exit_code; waiting for a model-state change before retrying" >&2
  while [[ "$stopping" -eq 0 && "$(state_fingerprint)" == "$fingerprint" ]]; do
    sleep 5
  done
done
