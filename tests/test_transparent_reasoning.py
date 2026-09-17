from __future__ import annotations

from dataclasses import replace

from app.main import create_app
from app.mcp.orchestrator import DynamicOrchestrator
from fastapi.testclient import TestClient
from test_dynamic_mcp import (
    FakeMCPClient,
    ToolProvider,
    _sse_chunks,
    dynamic_settings,
    select_model,
)


class FailingMCPClient(FakeMCPClient):
    async def call_tool(self, service, name, arguments):
        raise RuntimeError("simulated MCP failure")


def test_detailed_trace_exposes_mcp_invocation_and_result_preview(tmp_path):
    config = dynamic_settings(tmp_path)
    select_model(config)
    provider = ToolProvider()

    with TestClient(create_app(provider=provider, settings=config, mcp_client=FakeMCPClient())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "search hello"}]},
        )

    assert response.status_code == 200
    payload = response.json()
    activity = payload["lmctl"]["activity"]
    started = next(item for item in activity if item["event"] == "tool_call_started")
    completed = next(item for item in activity if item["event"] == "tool_call_completed")

    assert payload["lmctl"]["explanation_mode"] == "tool_trace"
    assert provider.turn == 3
    assert started["provider"] == "search-provider"
    assert started["endpoint"] == "http://search-provider:9000/mcp"
    assert started["transport"] == "streamable-http"
    assert started["mcp_request"] == {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "hello"}},
    }
    assert "### Calling tool: search-provider.search" in started["message"]
    assert '"method": "tools/call"' in started["message"]
    assert "result for hello" in completed["result_preview"]
    assert completed["result_preview_truncated"] is False
    assert not any(item["event"] == "reasoning_summary" for item in activity)
    assert not any("reasoning_content" in item for item in activity)


def test_tool_exception_stays_failed_in_the_trace(tmp_path):
    config = dynamic_settings(tmp_path)
    select_model(config)

    with TestClient(
        create_app(provider=ToolProvider(), settings=config, mcp_client=FailingMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "search hello"}]},
        )

    assert response.status_code == 200
    activity = response.json()["lmctl"]["activity"]
    search_events = [item for item in activity if item.get("openai_tool") == "mcp__search-provider__search"]
    failed = next(item for item in search_events if item["event"] == "tool_call_failed")

    assert not any(item["event"] == "tool_call_completed" for item in search_events)
    assert failed["error"] == "RuntimeError"
    assert failed["result_is_error"] is True
    assert "simulated MCP failure" in failed["error_detail"]
    assert "MCP call failed: RuntimeError" in failed["result_preview"]


def test_detailed_trace_is_streamed_and_can_be_compacted(tmp_path):
    config = dynamic_settings(tmp_path)
    select_model(config)

    with TestClient(
        create_app(provider=ToolProvider(), settings=config, mcp_client=FakeMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "stream": True,
                "messages": [{"role": "user", "content": "search hello"}],
            },
        )
        chunks = _sse_chunks(response.text)
        stream_activity = [chunk["lmctl_activity"] for chunk in chunks if "lmctl_activity" in chunk]

    assert response.status_code == 200
    started = next(item for item in stream_activity if item["event"] == "tool_call_started")
    assert started["mcp_request"]["params"]["arguments"] == {"query": "hello"}
    assert any(
        "tools/call" in chunk["choices"][0]["delta"].get("reasoning_content", "")
        for chunk in chunks
        if chunk.get("choices")
    )

    disabled_root = tmp_path / "disabled"
    disabled_root.mkdir()
    disabled = replace(dynamic_settings(disabled_root), explanation_enabled=False)
    select_model(disabled)
    with TestClient(
        create_app(provider=ToolProvider(), settings=disabled, mcp_client=FakeMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "search hello"}]},
        )

    assert response.status_code == 200
    payload = response.json()
    started = next(item for item in payload["lmctl"]["activity"] if item["event"] == "tool_call_started")
    assert payload["lmctl"]["explanation_mode"] == "compact_activity"
    assert started["message"] == "Calling tool: search-provider.search"
    assert started["mcp_request"]["params"]["arguments"] == {"query": "hello"}


def test_trace_redacts_credentials_and_bounds_previews():
    orchestrator = DynamicOrchestrator(manager=None, provider=None, tool_trace_preview_chars=128)

    redacted = orchestrator._redact_trace_value(
        {
            "query": "hello",
            "api_key": "should-not-appear",
            "nested": {"Authorization": "Bearer should-not-appear"},
        }
    )
    preview, truncated = orchestrator._trace_json({"payload": "x" * 200})

    assert redacted == {
        "query": "hello",
        "api_key": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]"},
    }
    assert "should-not-appear" not in str(redacted)
    assert truncated is True
    assert "truncated" in preview
