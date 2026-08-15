#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def load() -> list[dict[str, Any]]:
    path = Path(os.environ.get("MCP_CONFIG_PATH", "/config/servers.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in payload["servers"] if item.get("category") == "general"]


def reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


async def probe_tools(port: int) -> tuple[int | None, str | None]:
    try:
        async with streamablehttp_client(
            f"http://127.0.0.1:{port}/mcp",
            timeout=5,
            sse_read_timeout=10,
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await asyncio.wait_for(session.initialize(), timeout=10)
                result = await asyncio.wait_for(session.list_tools(), timeout=10)
                return len(result.tools), None
    except Exception as exc:
        return None, type(exc).__name__


async def check(probe: bool) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for item in load():
        port = int(item["port"])
        is_reachable = reachable(port)
        statuses.append(
            {
                "name": item["name"],
                "port": port,
                "status": "OK" if is_reachable else "DOWN",
            }
        )
    if probe:
        results = await asyncio.gather(
            *(probe_tools(item["port"]) for item in statuses if item["status"] == "OK")
        )
        result_iter = iter(results)
        for item in statuses:
            if item["status"] != "OK":
                continue
            tool_count, error = next(result_iter)
            if error:
                item["status"] = "PROTOCOL_ERROR"
                item["error"] = error
            else:
                item["tools"] = tool_count
    return statuses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--probe-tools", action="store_true")
    args = parser.parse_args()
    statuses = asyncio.run(check(args.probe_tools))
    if args.json:
        print(json.dumps({"servers": statuses}, indent=2))
    else:
        for item in statuses:
            detail = f" tools={item['tools']}" if "tools" in item else ""
            if "error" in item:
                detail = f" error={item['error']}"
            print(f"{item['name']:<22} {item['status']}{detail}")
    return 0 if statuses and all(item["status"] == "OK" for item in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
