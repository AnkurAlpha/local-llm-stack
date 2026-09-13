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
    with TestClient(
        create_app(provider=provider, settings=config, mcp_client=FakeMCPClient())
    ) as client:
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
        assert response.json()["choices"][0]["message"]["content"] == "done"

    assert provider.tool_lists[0] == ["lmctl_activate_skill"]
    assert "mcp__search-provider__search" in provider.tool_lists[1]
    assert all("mcp__broken" not in name for name in provider.tool_lists[1])
