# Validation record

This records checks performed in the authoring environment on 2026-08-10. It separates executed
results from host-dependent checks that still need a Docker/NVIDIA machine.

## Passed here

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

## Not executable here

The authoring environment has no `docker` executable (`docker compose config` exits 127). Therefore
it could not truthfully execute:

- Docker Compose's own `config` renderer;
- custom image builds or upstream image pulls;
- NVIDIA driver/Container Toolkit checks;
- live llama.cpp loading/inference;
- AnythingLLM-to-llama.cpp or Agent-API-to-llama.cpp container smoke tests;
- live MCP protocol checks inside Compose; or
- Chroma/memory-MCP persistence across container recreation.

## Run on the target host

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
