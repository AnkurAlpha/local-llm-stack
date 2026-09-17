from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.main import create_app
from app.providers.base import ChatProvider
from fastapi.testclient import TestClient


class FakeMCPClient:
    async def list_tools(self, service) -> list[dict[str, Any]]:
        if service.name == "broken":
            raise ConnectionError("offline")
        return [
            {
                "name": "search",
                "description": "Search the test internet index.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    async def call_tool(self, service, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"result for {arguments['query']}"}]}


class ToolProvider(ChatProvider):
    def __init__(self) -> None:
        self.tool_lists: list[list[str]] = []
        self.turn = 0

    async def health(self) -> bool:
        return True

    async def models(self) -> list[dict[str, Any]]:
        return [{"id": "local-model"}]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature=None,
        max_tokens=None,
        tools=None,
    ) -> dict[str, Any]:
        names = [tool["function"]["name"] for tool in tools or []]
        self.tool_lists.append(names)
        self.turn += 1
        if self.turn == 1:
            call = {
                "id": "activate-1",
                "type": "function",
                "function": {"name": "lmctl_activate_skill", "arguments": '{"skill_id":"internet"}'},
            }
            return {"model": "local-model", "choices": [{"message": {"tool_calls": [call]}}]}
        if self.turn == 2:
            call = {
                "id": "search-1",
                "type": "function",
                "function": {
                    "name": "mcp__search-provider__search",
                    "arguments": '{"query":"hello"}',
                },
            }
            return {"model": "local-model", "choices": [{"message": {"tool_calls": [call]}}]}
        return {
            "model": "local-model",
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
        }


def dynamic_settings(tmp_path: Path) -> Settings:
    state = tmp_path / "state"
    state.mkdir()
    manifest = tmp_path / "servers.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": [
                    {
                        "name": "search-provider",
                        "category": "general",
                        "category_hint": ["internet"],
                        "transport": "streamable-http",
                        "url": "http://search-provider:9000/mcp",
                    },
                    {
                        "name": "broken",
                        "category": "general",
                        "category_hint": ["database"],
                        "transport": "streamable-http",
                        "url": "http://broken:9001/mcp",
                    },
                ],
            }
        )
    )
    return Settings(
        llama_base_url="http://llama-cpp:8080/v1",
        llama_model_alias="local-model",
        models_root=tmp_path / "models",
        state_root=state,
        mcp_config_path=manifest,
        request_timeout=10,
        log_level="INFO",
        skills_path=tmp_path / "skills",
        mcp_state_path=tmp_path / "discovery.json",
        mcp_discovery_timeout=1,
        mcp_discovery_retries=0,
        mcp_discovery_retry_delay=0,
        dynamic_max_steps=5,
    )


def select_model(config: Settings) -> None:
    (config.state_root / "current-model.json").write_text(
        json.dumps({"model_id": "test-model", "primary_file": "owner/repo/model.gguf"})
    )


def test_discovery_failure_isolated_and_tool_schemas_are_progressive(tmp_path: Path) -> None:
    config = dynamic_settings(tmp_path)
    select_model(config)
    provider = ToolProvider()
    with TestClient(create_app(provider=provider, settings=config, mcp_client=FakeMCPClient())) as client:
        status = client.get("/mcp/status").json()
        assert status["status"] == "degraded"
        assert {item["name"] for item in status["servers"]} == {"search-provider", "broken"}
        skills = client.get("/mcp/skills").json()["skills"]
        assert {item["id"] for item in skills} >= {"internet", "database"}

        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "search hello"}]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["choices"][0]["message"]["content"] == "done"
        activity = payload["lmctl"]["activity"]
        assert any(item["event"] == "skill_activated" for item in activity)
        assert any(item["event"] == "tool_call_completed" for item in activity)

    assert provider.tool_lists[0] == ["lmctl_activate_skill"]
    assert "mcp__search-provider__search" in provider.tool_lists[1]
    assert all("mcp__broken" not in name for name in provider.tool_lists[1])


def _sse_chunks(text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        value = line.removeprefix("data: ")
        if value != "[DONE]":
            chunks.append(json.loads(value))
    return chunks


def test_stream_exposes_activity_without_loading_unrelated_schemas(tmp_path: Path) -> None:
    config = dynamic_settings(tmp_path)
    select_model(config)
    provider = ToolProvider()
    with TestClient(create_app(provider=provider, settings=config, mcp_client=FakeMCPClient())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "stream": True,
                "messages": [{"role": "user", "content": "search hello"}],
            },
        )
        assert response.status_code == 200
        chunks = _sse_chunks(response.text)
        activity = [chunk["lmctl_activity"] for chunk in chunks if "lmctl_activity" in chunk]
        messages = [item["message"] for item in activity]
        assert any("Activated skill: internet" in message for message in messages)
        assert any("Loaded tools: search-provider.search" in message for message in messages)
        assert any("Calling tool: search-provider.search" in message for message in messages)
        assert any("Tool completed: search-provider.search" in message for message in messages)
        assert any("Generating final response" in message for message in messages)
        content = "".join(
            chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk.get("choices")
        )
        assert content == "done"

        latest = client.get("/mcp/activity").json()["latest"]
        assert latest["request_id"] == chunks[0]["id"]
        assert len(latest["events"]) == len(activity)

    assert provider.tool_lists[0] == ["lmctl_activate_skill"]
    assert "mcp__search-provider__search" in provider.tool_lists[1]
    assert all("mcp__broken" not in name for name in provider.tool_lists[1])
