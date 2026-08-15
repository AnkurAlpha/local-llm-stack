#!/usr/bin/env python3
"""Generate client-specific MCP configuration from the canonical manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config" / "mcp" / "servers.json"
ANYTHINGLLM = ROOT / "config" / "mcp" / "anythingllm_mcp_servers.json"


def render_anythingllm(source: Path = SOURCE) -> str:
    manifest = json.loads(source.read_text(encoding="utf-8"))
    servers: dict[str, dict[str, str]] = {}
    for server in manifest["servers"]:
        transport = server["transport"]
        if transport not in {"streamable-http", "sse"}:
            continue
        servers[server["name"]] = {
            "type": "streamable" if transport == "streamable-http" else "sse",
            "url": server["url"],
        }
    return json.dumps({"mcpServers": servers}, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_anythingllm()
    if args.check:
        if not ANYTHINGLLM.exists() or ANYTHINGLLM.read_text(encoding="utf-8") != rendered:
            print(f"out of date: {ANYTHINGLLM}")
            return 1
        print("MCP client configuration is current")
        return 0
    ANYTHINGLLM.write_text(rendered, encoding="utf-8")
    print(f"generated {ANYTHINGLLM.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
