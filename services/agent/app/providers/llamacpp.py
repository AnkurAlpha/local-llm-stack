from __future__ import annotations

from typing import Any

import httpx

from .base import ChatProvider


class LlamaCppProvider(ChatProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.server_url = self.base_url.removesuffix("/v1")
        self.model = model
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10))

    async def health(self) -> bool:
        try:
            response = await self.client.get(f"{self.server_url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def models(self) -> list[dict[str, Any]]:
        response = await self.client.get(f"{self.base_url}/models")
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("data", []))

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = await self.client.post(f"{self.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return dict(response.json())

    async def close(self) -> None:
        await self.client.aclose()
