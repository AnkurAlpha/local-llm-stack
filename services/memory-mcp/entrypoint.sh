#!/usr/bin/env bash
set -Eeuo pipefail

child_pid=""
stop=0
terminate() {
  stop=1
  [[ -n "$child_pid" ]] && kill -TERM "$child_pid" 2>/dev/null || true
}
trap terminate TERM INT

while [[ "$stop" -eq 0 ]]; do
  echo '{"level":"INFO","service":"memory-mcp","message":"starting stdio-to-Streamable-HTTP bridge"}'
  supergateway \
    --stdio "python /app/server.py" \
    --outputTransport streamableHttp \
    --port "${MEMORY_MCP_PORT:-8011}" \
    --streamableHttpPath /mcp \
    --cors "${MCP_CORS_ORIGIN:-http://localhost:3001}" \
    --logLevel info &
  child_pid=$!
  set +e
  wait "$child_pid"
  code=$?
  set -e
  child_pid=""
  [[ "$stop" -eq 1 ]] && break
  echo "{\"level\":\"ERROR\",\"service\":\"memory-mcp\",\"message\":\"bridge exited; retrying\",\"exit_code\":$code}" >&2
  sleep "${MCP_RESTART_DELAY:-5}"
done

