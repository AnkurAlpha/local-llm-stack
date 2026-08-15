# Migration from `internet.sh` and `mcp_setup.sh`

## Source inventory

The supplied scripts define exactly eight MCP servers:

1. DuckDuckGo (`duckduckgo-mcp-server`)
2. Sequential Thinking (`@modelcontextprotocol/server-sequential-thinking`)
3. Fetch (`mcp-server-fetch`)
4. Time (`mcp-server-time`, timezone `Asia/Kolkata`)
5. SQLite (`mcp-server-sqlite`)
6. Context7 (`@upstash/context7-mcp`)
7. Playwright (`@playwright/mcp`)
8. Chroma Memory (custom FastMCP Python server)

No filesystem MCP or other server was added because it was not present in the supplied scripts.

## Preserved behavior

- DuckDuckGo remains native Streamable HTTP.
- Every original stdio server remains stdio internally and is bridged by Supergateway to
  Streamable HTTP at `/mcp`.
- Original internal port numbers are retained.
- Time defaults to `Asia/Kolkata`.
- SQLite remains an ephemeral `test.db` with the original `notes` table.
- Playwright retains a runtime profile, which remains ephemeral like the old `/tmp` directory.
- The memory MCP retains all five original tool names, arguments, bounds, and text responses.
- Chroma remains pinned to the script's `chromadb/chroma:1.5.3`.

The uploaded setup script's Python heredoc had lost required indentation. The implementation in
`services/memory-mcp/` restores the intended indentation without changing tool behavior and adds a
bounded Chroma readiness retry.

## Necessary container changes

| Old value/behavior | Compose value/behavior | Reason |
| --- | --- | --- |
| `127.0.0.1` between processes | Docker DNS (`mcp-tools`, `memory-mcp`, `chroma`) | `localhost` cannot cross containers |
| `npx -y` / `uvx` every startup | pinned build-time packages | reproducible and faster startup |
| Chroma launched inside shell script | separate `chroma` service | independent health/lifecycle/storage |
| memory MCP launched with general tools | separate `memory-mcp` service | required independent lifecycle |
| model-specific Docker named volume | `data/memory/chroma:/data` | host-visible backup and portability |
| host MCP ports | Docker-internal ports only | avoids port conflict and unnecessary exposure |
| CORS origin `http://localhost:8080` | configurable, default `http://localhost:3001` | AnythingLLM is now the browser UI |
| Playwright desktop defaults | pinned Chromium, headless, no browser sandbox | required for reliable container execution |
| `pkill` and temporary PID files | container supervisor + Docker restart policy | container-scoped process lifecycle |

## Canonical configuration

`config/mcp/servers.json` is the single human-edited manifest. It drives:

- `mcp-tools` process supervision;
- Agent API discovery;
- health/status reporting; and
- generated `config/mcp/anythingllm_mcp_servers.json`.

Regenerate and verify with:

```bash
python3 scripts/generate_mcp_configs.py
python3 scripts/generate_mcp_configs.py --check
```

## Existing Chroma volume

Do not delete the old volume. With an empty new destination, run:

```bash
./llmctl memory import-volume chroma-memory-volume-MODEL_NAME
```

The source is mounted read-only and retained. The command refuses an occupied destination, then
creates a backup after import. Use the same Chroma version for the first verification. A different
on-disk format must be migrated using Chroma's version-specific tooling rather than reset.

## Persistence verification procedure

On a Docker-capable host, use an isolated test collection or delete only the individual test memory
through its exact ID afterward:

1. Start the stack and confirm `./llmctl memory status`.
2. Call `remember_memory` with a unique harmless marker.
3. Call `search_memory` and record its exact ID.
4. Run `docker compose restart memory-mcp chroma` (do not use `down -v`).
5. Search for the marker again.
6. Call `delete_memory` with the recorded ID.

This execution environment did not have Docker, so the repository records that procedure separately
from unit tests and does not claim a live persistence result unless it was actually run.
