from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from app.main import create_app
from app.providers.errors import provider_error_detail
from fastapi.testclient import TestClient
from test_dynamic_mcp import (
    FakeMCPClient,
    ToolProvider,
    _sse_chunks,
    dynamic_settings,
    select_model,
)


class ErrorResultMCP(FakeMCPClient):
    async def call_tool(self, service, name, arguments):
        return {
            "isError": True,
            "content": [{"type": "text", "text": "Invalid probe arguments"}],
        }


class RejectedProvider(ToolProvider):
    async def chat(self, messages, temperature=None, max_tokens=None, tools=None):
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "http://llama-cpp:8080/v1/chat/completions"),
            json={
                "error": {"message": "Tool calling requires a supported chat template"},
                "unrelated_private_field": "must-not-be-in-error-output",
            },
        )
        response.raise_for_status()


@pytest.mark.parametrize("stream", [False, True])
def test_mcp_error_result_is_failed_activity_and_retained_for_model(tmp_path, stream):
    config = dynamic_settings(tmp_path)
    select_model(config)

    class InspectingProvider(ToolProvider):
        async def chat(self, messages, **kwargs):
            if self.turn == 2:
                assert json.loads(messages[-1]["content"])["isError"] is True
            return await super().chat(messages, **kwargs)

    with TestClient(
        create_app(provider=InspectingProvider(), settings=config, mcp_client=ErrorResultMCP())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"stream": stream, "messages": [{"role": "user", "content": "search hello"}]},
        )
        assert response.status_code == 200
        events = client.get("/mcp/activity").json()["latest"]["events"]
        assert any(event["event"] == "tool_call_failed" for event in events)
        assert not any(event["event"] == "tool_call_completed" for event in events)


@pytest.mark.parametrize("stream", [False, True])
def test_http_failure_has_status_reason_and_activity_history(tmp_path, stream):
    config = dynamic_settings(tmp_path)
    select_model(config)
    with TestClient(
        create_app(provider=RejectedProvider(), settings=config, mcp_client=FakeMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"stream": stream, "messages": [{"role": "user", "content": "search hello"}]},
        )
        assert response.status_code == (200 if stream else 502)
        assert "HTTP 400" in response.text
        assert "supported chat template" in response.text
        assert "must-not-be-in-error-output" not in response.text
        latest = client.get("/mcp/activity").json()["latest"]
        assert latest is not None
        assert latest["events"][-1]["event"] == "request_failed"
        assert "HTTP 400" in latest["events"][-1]["message"]
        if stream:
            assert response.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("stream", [False, True])
def test_step_limit_gives_visible_incomplete_answer_without_pending_calls(tmp_path, stream):
    config = replace(dynamic_settings(tmp_path), dynamic_max_steps=1)
    select_model(config)
    provider = ToolProvider()
    with TestClient(
        create_app(provider=provider, settings=config, mcp_client=FakeMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"stream": stream, "messages": [{"role": "user", "content": "search hello"}]},
        )
        assert response.status_code == 200
        if stream:
            chunks = _sse_chunks(response.text)
            content = "".join(
                chunk["choices"][0]["delta"].get("content", "")
                for chunk in chunks if chunk.get("choices")
            )
        else:
            message = response.json()["choices"][0]["message"]
            assert not message.get("tool_calls")
            content = message["content"]
        assert "limit" in content.lower()
        assert "complete" in content.lower()
        assert provider.turn == 1  # The loop budget is actually bounded.


@pytest.mark.parametrize("stream", [False, True])
def test_exhausted_token_budget_does_not_produce_a_blank_answer(tmp_path, stream):
    config = dynamic_settings(tmp_path)
    select_model(config)

    class EmptyProvider(ToolProvider):
        async def chat(self, messages, **kwargs):
            return {"choices": [{
                "message": {"role": "assistant", "content": "", "reasoning_content": "unfinished"},
                "finish_reason": "length",
            }]}

    with TestClient(
        create_app(provider=EmptyProvider(), settings=config, mcp_client=FakeMCPClient())
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"stream": stream, "messages": [{"role": "user", "content": "search hello"}]},
        )
        assert "token" in response.text.lower()
        events = client.get("/mcp/activity").json()["latest"]["events"]
        assert any(event["event"] == "warning" for event in events)


def test_http_diagnostic_limits_and_redacts_message_and_ignores_raw_body():
    request = httpx.Request("POST", "http://llama-cpp:8080/v1/chat/completions")
    response = httpx.Response(401, request=request, json={"error": {
        "message": "Invalid api_key=super-secret Bearer hidden-auth " + "x" * 2000,
    }})
    detail = provider_error_detail(httpx.HTTPStatusError("failed", request=request, response=response))
    assert "HTTP 401" in detail
    assert "super-secret" not in detail and "hidden-auth" not in detail
    assert len(detail) < 900
    response = httpx.Response(502, request=request, text="<html>private upstream page</html>")
    detail = provider_error_detail(httpx.HTTPStatusError("failed", request=request, response=response))
    assert "HTTP 502" in detail
    assert "private upstream" not in detail
