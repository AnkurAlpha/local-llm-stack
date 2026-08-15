from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .models import MCPService


class MCPClient:
    """Small SDK-backed boundary for future agent tool invocation."""

    async def list_tools(self, service: MCPService) -> list[dict[str, Any]]:
        if service.transport != "streamable-http":
            raise ValueError(f"unsupported MCP transport: {service.transport}")
        async with streamablehttp_client(service.url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return [tool.model_dump(mode="json") for tool in result.tools]
