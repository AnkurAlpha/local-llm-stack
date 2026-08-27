# Validation record

This records checks performed for the V1.0.0 release. Authoring-environment checks were run on
2026-08-10; live target-host checks were run on 2026-08-27.

## Passed in the authoring environment

- Ruff formatting and linting across all Python sources.
- Python bytecode compilation for services, scripts, and tests.
- Bash syntax for `llmctl`, the llama.cpp launcher/health check, and the memory entrypoint.
- Canonical-to-AnythingLLM MCP configuration regeneration check.
- 36 non-live pytest tests covering model choice/shards, traversal protection, integrity checks,
  registry/state, stale selection handling, model-manager CLI, Agent API, MCP configuration,
  memory tool behavior, and Compose invariants.
- One opt-in live Hugging Face test against `ggml-org/Qwen3.5-0.8B-GGUF`; repository metadata,
  commit revision, GGUF grouping, download URL resolution, and content size resolved without
  downloading model weights.
- Exact Python dependency resolution for all four custom images.
- Actual install and CLI-option verification for DuckDuckGo, Fetch, Time, and SQLite MCP packages.
- Actual npm package/binary verification for Supergateway, Sequential Thinking, Context7,
  Playwright MCP, and the exact Playwright runtime.
- Direct model-manager `health`, empty `list`, empty `current`, and invalid-repository CLI checks.
- Host-side `./llmctl mcp list` check.
- `./llmctl doctor` correctly reported `Docker: MISSING` and returned a failure status in this host.
- Compose YAML parsing and structural/invariant tests, including service health checks, loopback-only
  public ports, internal Docker DNS endpoints, Chroma persistence, and the CUDA GPU overlay.

## Passed on the target CUDA host (2026-08-27)

The following live checks were run on the CUDA-enabled Linux host with the selected model
`unsloth--Qwen3-8B-GGUF--Qwen3-8B-Q4_K_M.gguf`:

- Agent API health reported `llama_ready: true` and `model_selected: true`.
- `POST /chat` returned the exact marker `LMCTL_AGENT_API_OK`.
- `.venv/bin/python -m pytest -q`: 36 passed, 1 deselected, 1 warning.
- `.venv/bin/ruff check .`: all checks passed.
- `.venv/bin/ruff format --check .`: 46 files already formatted.
- `docker compose config --quiet` passed.
- `git diff --check` passed.
- `./llmctl status` showed all services healthy and NVIDIA CUDA available.
- All 7 general MCP servers reported `OK`.
- Memory MCP and Chroma reported `OK` with persistent storage.
- AnythingLLM successfully invoked web scraping for `https://example.com` and returned
  `Example Domain` as the page title.

These results complete the V1 functional and host-side regression validation. The published
release is [v1.0.0](https://github.com/AnkurAlpha/local-llm-stack/releases/tag/v1.0.0).


## Reproduce on another host

```bash
cp .env.example .env
./llmctl doctor
docker compose config
./llmctl up
./llmctl status
./llmctl mcp status
./llmctl memory status
```

After selecting a small test GGUF, verify `POST /chat` as shown in the README. Follow the isolated
marker procedure in `docs/MCP_MIGRATION.md` to verify memory persistence and remove the marker by its
exact ID afterward.

`chromadb==1.5.3` is an exact, installable but yanked PyPI release. It remains intentional in V1 to
match the supplied Chroma 1.5.3 setup and existing on-disk data. Treat its coordinated upgrade as a
backed-up migration, not an incidental dependency bump.
