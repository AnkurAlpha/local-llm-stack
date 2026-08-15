#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def log(level: str, message: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "service": "mcp-tools",
        "message": message,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def expand(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"required MCP environment variable is missing: {name}")
        return os.environ[name]

    return ENV_RE.sub(replace, value)


def load_servers(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported MCP manifest version")
    servers = [item for item in payload.get("servers", []) if item.get("category") == "general"]
    if not servers:
        raise ValueError("canonical MCP manifest contains no general servers")
    return servers


def command_for(server: dict[str, Any]) -> list[str]:
    runtime = server["runtime"]
    source = [expand(str(value)) for value in runtime["command"]]
    if not source or any("\x00" in value for value in source):
        raise ValueError(f"invalid command for {server['name']}")
    if runtime["mode"] == "direct":
        return source
    if runtime["mode"] != "supergateway":
        raise ValueError(f"unsupported runtime mode for {server['name']}: {runtime['mode']}")
    return [
        "supergateway",
        "--stdio",
        shlex.join(source),
        "--outputTransport",
        "streamableHttp",
        "--port",
        str(int(server["port"])),
        "--streamableHttpPath",
        "/mcp",
        "--cors",
        os.environ.get("MCP_CORS_ORIGIN", "http://localhost:3001"),
        "--logLevel",
        "info",
    ]


async def emit_output(name: str, stream: asyncio.StreamReader) -> None:
    while line := await stream.readline():
        log("INFO", line.decode(errors="replace").rstrip(), mcp_server=name)


async def supervise(server: dict[str, Any], stop: asyncio.Event) -> None:
    name = str(server["name"])
    delay = max(1, int(os.environ.get("MCP_RESTART_DELAY", "5")))
    while not stop.is_set():
        command = command_for(server)
        log("INFO", "starting MCP server", mcp_server=name, port=server["port"])
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            assert process.stdout is not None
            output_task = asyncio.create_task(emit_output(name, process.stdout))
            code = await process.wait()
            await output_task
            log("ERROR", "MCP server exited", mcp_server=name, exit_code=code)
        except asyncio.CancelledError:
            if process and process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
            raise
        except Exception as exc:
            log("ERROR", "MCP server launch failed", mcp_server=name, error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


async def run() -> int:
    config = Path(os.environ.get("MCP_CONFIG_PATH", "/config/servers.json"))
    runtime = Path(os.environ.get("MCP_RUNTIME_DIR", "/runtime"))
    for directory in (
        runtime / "data",
        runtime / "home",
        runtime / "logs",
        runtime / "playwright-profile",
        runtime / "playwright-output",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(runtime / "data" / "test.db") as database:
        database.execute("CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, text TEXT)")

    servers = load_servers(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    tasks = [asyncio.create_task(supervise(server, stop)) for server in servers]
    log("INFO", "MCP supervisor ready", server_count=len(tasks))
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except (ValueError, json.JSONDecodeError) as exc:
        log("CRITICAL", str(exc))
        sys.exit(2)
