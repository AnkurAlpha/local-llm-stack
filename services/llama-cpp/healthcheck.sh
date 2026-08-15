#!/usr/bin/env bash
set -Eeuo pipefail

status_file=/state/llama-status.json
[[ -s "$status_file" ]] || exit 1
state="$(jq -r '.state // "UNKNOWN"' "$status_file")"
case "$state" in
  WAITING)
    # Waiting without a model is an intentional healthy platform state.
    exit 0
    ;;
  RUNNING)
    curl -fsS "http://127.0.0.1:${LLAMA_INTERNAL_PORT:-8080}/health" >/dev/null
    ;;
  *)
    exit 1
    ;;
esac

