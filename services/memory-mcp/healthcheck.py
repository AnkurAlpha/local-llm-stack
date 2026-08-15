#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def probe_tools(port: int) -> int:
    async with streamablehttp_client(
        f"http://127.0.0.1:{port}/mcp",
        timeout=5,
        sse_read_timeout=10,
    ) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await asyncio.wait_for(session.initialize(), timeout=10)
            result = await asyncio.wait_for(session.list_tools(), timeout=10)
            return len(result.tools)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-tools", action="store_true")
    args = parser.parse_args()
    host = os.getenv("CHROMA_HOST", "chroma")
    chroma_port = int(os.getenv("CHROMA_PORT", "8000"))
    mcp_port = int(os.getenv("MEMORY_MCP_PORT", "8011"))
    status = {"memory_mcp": "DOWN", "chroma": "DOWN"}
    try:
        with socket.create_connection(("127.0.0.1", mcp_port), timeout=2):
            status["memory_mcp"] = "OK"
    except OSError:
        pass
    if args.probe_tools and status["memory_mcp"] == "OK":
        try:
            status["tools"] = asyncio.run(probe_tools(mcp_port))
        except Exception as exc:
            status["memory_mcp"] = "PROTOCOL_ERROR"
            status["error"] = type(exc).__name__
    try:
        with urllib.request.urlopen(f"http://{host}:{chroma_port}/api/v2/heartbeat", timeout=3) as response:
            if response.status == 200:
                status["chroma"] = "OK"
    except OSError:
        pass
    print(json.dumps(status))
    return 0 if status["memory_mcp"] == "OK" and status["chroma"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
