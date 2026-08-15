# Architecture and extension boundaries

## Current request paths

AnythingLLM and Agent API both use the stable OpenAI-compatible alias `local-model` at
`http://llama-cpp:8080/v1`. The launcher resolves the physical GGUF from
`data/state/current-model.json`; clients never need the model's filesystem path.

The model manager owns all writes to the model registry and selection state. llama.cpp mounts
weights read-only. Agent API mounts both weights and state read-only. This prevents inference or
orchestration code from silently turning an incomplete download into a registered model.

## Later multi-model evolution

Prototype V1 deliberately uses one Compose service named `llama-cpp`. A later version can create a
generated instance definition for each specialized model:

```text
data/state/instances/
├── conversational.json
├── research.json
├── mathematics.json
└── coding-cpp.json
```

Each instance can reuse the launcher contract (`primary_file`, stable alias, status file), mount the
same read-only `/models`, and expose only an internal port. The Agent API's provider factory can map a
logical role to one provider URL. No GGUF layout, download record, MCP endpoint, or AnythingLLM data
format needs to change.

## Agent boundaries

- `app.providers.ChatProvider`: inference-provider contract.
- `app.mcp.MCPClient`: MCP protocol contract.
- `app.mcp.MCPRegistry`: canonical service discovery.
- `app.agents`: future router/planner/specialist implementations.
- `app.tools`: future agent-local tools only.

Specialized agents should receive provider and MCP dependencies through constructors. They should not
read Compose files, invoke Docker, or hardcode service URLs.

## Network and trust boundary

The bridge network is not marked `internal: true` because the supplied DuckDuckGo, Fetch, Context7,
and Playwright tools require outbound internet access, and the model manager requires Hugging Face.
No MCP or Chroma port is published to the host. Public host access is loopback-only for the three user
interfaces/APIs.

Playwright is powerful: treat pages and downloaded content as untrusted. Do not place secrets in MCP
configuration. Add future secrets through `.env` or Docker secrets and pass them only to the one
service that needs them.

## Health semantics

The llama launcher writes one of `WAITING`, `STARTING`, `RUNNING`, or `ERROR` to
`data/state/llama-status.json`. `WAITING` is Docker-healthy by design, allowing the rest of the stack
to start without a model. `RUNNING` is healthy only when llama.cpp's real `/health` endpoint responds.

MCP health checks open every configured service port. Agent API can additionally perform MCP SDK
initialization and `tools/list`. Chroma uses `/api/v2/heartbeat`. AnythingLLM uses `/api/ping`.

