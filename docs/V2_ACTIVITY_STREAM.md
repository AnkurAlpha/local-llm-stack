# LMCTL V2 activity and MCP tool trace

## Purpose

AnythingLLM receives the Agent API's OpenAI-compatible response. LMCTL keeps
the dynamic registry as the only source of MCP tool schemas, but its
user-visible activity stream shows what occurred during a request.

With detailed tracing enabled, the collapsible AnythingLLM thought panel shows:

- the model-requested skill activation and its arguments;
- the tools that became active for that skill;
- each MCP endpoint, OpenAI function name, call ID, and tools/call JSON-RPC
  request shape;
- a bounded result preview, duration, status, and errors; and
- rejected calls and bounded-loop stop reasons.

This is an execution trace, not a copy of private model chain-of-thought. It
is generated only from observable LMCTL state transitions and adds no model
turns or MCP schemas.

MCP tools are not terminal commands. They are invoked through the MCP protocol,
so the trace shows the exact LMCTL tools/call payload and endpoint used for
each call. Normal arguments are displayed; fields with credential-like names
such as api_key, token, password, or cookie are redacted.

## Data flow

~~~mermaid
sequenceDiagram
    participant UI as AnythingLLM
    participant API as Agent API
    participant MCP as Dynamic MCP registry
    UI->>API: streaming chat request
    API->>MCP: activate skill and call discovered tool
    MCP-->>API: tool result
    API-->>UI: detailed activity chunks
    API-->>UI: final answer
~~~

For streaming requests, each event is emitted as an OpenAI-compatible SSE
chunk whose delta contains reasoning_content, which AnythingLLM renders in
its collapsible activity panel. The same event is also attached as the
structured lmctl_activity field for API clients. The final answer remains
ordinary delta.content.

## Example tool trace

~~~text
### Activated skill: internet
Model-requested function: lmctl_activate_skill
Arguments: {"skill_id": "internet"}
Providers: duckduckgo, fetch

### Loaded tools: duckduckgo.search, duckduckgo.fetch_content, fetch.fetch

### Calling tool: duckduckgo.search
OpenAI function: mcp__duckduckgo__search
MCP endpoint: http://mcp-tools:8000/mcp (streamable-http)
MCP tools/call request:
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {"name": "search", "arguments": {"query": "latest llama.cpp"}}
}

### Tool completed: duckduckgo.search
Duration: 1319.3 ms
MCP result preview: ...
~~~

The provider and tool names come from live discovery. A future MCP therefore
gets the same trace without a hard-coded per-server implementation.

## Diagnostics

The bounded, in-memory history is available while Agent API is running:

~~~bash
./llmctl activity
curl -fsS http://localhost:8000/mcp/activity | jq
~~~

The normal non-streaming Agent API response also contains lmctl.activity and
lmctl.context. Activity history is ephemeral. Restarting Agent API clears it;
it is not written to Chroma memory or to the discovery snapshot.

## Configuration

~~~dotenv
LMCTL_ACTIVITY_ENABLED=true
LMCTL_ACTIVITY_MAX_EVENTS=64
LMCTL_ACTIVITY_HISTORY_SIZE=20
LMCTL_EXPLANATION_ENABLED=true
LMCTL_TOOL_TRACE_PREVIEW_CHARS=4000
~~~

Set LMCTL_EXPLANATION_ENABLED=false for concise operational activity instead
of the detailed trace. This does not change MCP permissions, loaded schemas,
or tool execution.

LMCTL_TOOL_TRACE_PREVIEW_CHARS bounds each displayed request/result preview.
Raise it if a local-only setup needs longer previews; sensitive named fields
remain redacted.

## Troubleshooting

1. Confirm AnythingLLM uses http://agent-api:8000/v1.
2. Confirm the image is mintplexlabs/anythingllm:1.16.1.
3. Confirm trace configuration inside the Agent API:

   ~~~bash
   docker compose exec -T agent-api env | rg '^LMCTL_(ACTIVITY|EXPLANATION|TOOL_TRACE)'
   ~~~

4. Make a tool-using request, then inspect raw history:

   ~~~bash
   ./llmctl activity
   curl -fsS http://localhost:8000/mcp/activity | jq '.latest.events'
   ~~~

5. If AnythingLLM does not show the panel, inspect the raw stream. It should
   contain both reasoning_content and lmctl_activity:

   ~~~bash
   curl -N -fsS http://localhost:8000/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"local-model","stream":true,"messages":[{"role":"user","content":"Use the internet skill to search for the latest llama.cpp release."}]}'
   ~~~

The trace is metadata only. It must not make all MCP schemas active or bypass
the V2 progressive-loading registry.
