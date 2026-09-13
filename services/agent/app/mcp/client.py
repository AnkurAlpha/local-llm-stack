from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .models import MCPService


class MCPClient:
    """Protocol boundary for MCP initialization, discovery, and invocation."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _dump(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return dict(value.model_dump(mode="json", by_alias=True))
        if isinstance(value, dict):
            return dict(value)
        raise TypeError(f"unsupported MCP result type: {type(value).__name__}")

    def _client(self, service: MCPService):
        if service.transport != "streamable-http":
            raise ValueError(f"unsupported MCP transport: {service.transport}")
        return streamablehttp_client(
            service.url,
            timeout=self.timeout,
            sse_read_timeout=max(self.timeout, 30.0),
        )

    async def list_tools(self, service: MCPService) -> list[dict[str, Any]]:
        async with self._client(service) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.list_tools()
                return [self._dump(tool) for tool in result.tools]

    async def call_tool(
        self,
        service: MCPService,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._client(service) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
                return self._dump(result)
