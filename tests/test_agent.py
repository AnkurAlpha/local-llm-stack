from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.main import create_app
from app.providers.base import ChatProvider
from fastapi.testclient import TestClient


class FakeProvider(ChatProvider):
    def __init__(self) -> None:
        self.closed = False

    async def health(self) -> bool:
        return True

    async def models(self) -> list[dict[str, Any]]:
        return [{"id": "local-model"}]

    async def chat(self, messages: list[dict[str, str]], temperature=None, max_tokens=None) -> dict[str, Any]:
        return {
            "model": "local-model",
            "choices": [
                {"message": {"content": f"echo: {messages[-1]['content']}"}, "finish_reason": "stop"}
            ],
            "usage": {"total_tokens": 4},
        }

    async def close(self) -> None:
        self.closed = True


def settings(tmp_path: Path) -> Settings:
    state = tmp_path / "state"
    state.mkdir()
    mcp = tmp_path / "servers.json"
    mcp.write_text('{"version":1,"servers":[]}')
    return Settings(
        llama_base_url="http://llama-cpp:8080/v1",
        llama_model_alias="local-model",
        models_root=tmp_path / "models",
        state_root=state,
        mcp_config_path=mcp,
        request_timeout=10,
        log_level="INFO",
    )


def select_model(config: Settings) -> None:
    (config.state_root / "current-model.json").write_text(
        json.dumps({"model_id": "test-model", "primary_file": "owner/repo/model.gguf"})
    )


def test_health_and_chat(tmp_path: Path) -> None:
    config = settings(tmp_path)
    select_model(config)
    provider = FakeProvider()
    with TestClient(create_app(provider=provider, settings=config)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["llama_ready"] is True
        response = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})
        assert response.status_code == 200
        assert response.json()["content"] == "echo: hello"
    assert provider.closed


def test_chat_requires_selection(tmp_path: Path) -> None:
    with TestClient(create_app(provider=FakeProvider(), settings=settings(tmp_path))) as client:
        response = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})
        assert response.status_code == 503
