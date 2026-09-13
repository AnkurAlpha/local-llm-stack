# MCP registry

These YAML files are the human-maintained MCP registry. They contain endpoint,
transport, runtime, and category hints only. They intentionally do not contain
tool names or JSON schemas.

At startup, LMCTL reads the generated manifest and the Agent API performs MCP
initialization followed by `tools/list`. The MCP server remains the source of
truth for its actual tools and input schemas.

To add an MCP:

1. Install it in `services/mcp-tools/Dockerfile`, or run it in another service.
2. Add one enabled YAML file here.
3. Run `./llmctl up` or `python3 scripts/generate_mcp_configs.py`.

For an MCP hosted outside `mcp-tools`, use `runtime.mode: external` and set its
Docker-DNS endpoint. The supervisor will not try to launch it, but the Agent
API will still discover it.
