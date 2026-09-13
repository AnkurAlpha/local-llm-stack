#!/usr/bin/env python3
"""Generate runtime MCP manifests from the declarative YAML registry.

The YAML files intentionally contain no tool schemas. The MCP servers remain
the source of truth for tools; the Agent API discovers those at runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config" / "mcps"
MANIFEST = ROOT / "config" / "mcp" / "servers.json"
ANYTHINGLLM_DIRECT = ROOT / "config" / "mcp" / "anythingllm_mcp_servers.json"
ANYTHINGLLM_DYNAMIC = ROOT / "config" / "mcp" / "anythingllm_mcp_servers_v2.json"


def _as_hints(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else str(value).split(",")
    return [str(item).strip().lower() for item in values if str(item).strip()]


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    identifier = str(payload.get("id", "")).strip()
    endpoint = str(payload.get("endpoint", "")).strip()
    if not identifier:
        raise ValueError(f"{path}: missing id")
    if not endpoint:
        raise ValueError(f"{path}: missing endpoint")
    if not bool(payload.get("enabled", True)):
        return {}

    runtime = payload.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ValueError(f"{path}: runtime must be a mapping")
    mode = str(runtime.get("mode", "external"))
    if mode not in {"direct", "supergateway", "external"}:
        raise ValueError(f"{path}: unsupported runtime.mode={mode}")
    transport = str(payload.get("transport", "streamable-http"))
    if transport not in {"streamable-http", "sse"}:
        raise ValueError(f"{path}: unsupported transport={transport}")
    hints = _as_hints(payload.get("category_hint"))
    scope = str(payload.get("scope", "memory" if "memory" in hints else "general"))
    if scope not in {"general", "memory"}:
        raise ValueError(f"{path}: scope must be general or memory")
    command = runtime.get("command")
    if command is not None and (
        not isinstance(command, list) or not all(isinstance(item, str) for item in command)
    ):
        raise ValueError(f"{path}: runtime.command must be a list of strings")

    server: dict[str, Any] = {
        "name": identifier,
        "category": scope,
        "category_hint": hints,
        "description": str(payload.get("description", "")).strip(),
        "transport": transport,
        "url": endpoint,
        "runtime": runtime,
    }
    if runtime.get("port") is not None:
        server["port"] = int(runtime["port"])
    if "source_transport" in payload:
        server["source_transport"] = str(payload["source_transport"])
    if isinstance(payload.get("storage"), dict):
        server["storage"] = dict(payload["storage"])
    return server


def load_registry(registry_dir: Path = REGISTRY_DIR) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(registry_dir.glob("*.yaml")):
        server = _load_yaml(path)
        if not server:
            continue
        name = str(server["name"])
        if name in seen:
            raise ValueError(f"duplicate MCP id: {name}")
        seen.add(name)
        servers.append(server)
    if not servers:
        raise ValueError(f"no enabled MCP definitions found in {registry_dir}")
    return servers


def render_manifest(servers: list[dict[str, Any]] | None = None) -> str:
    servers = servers if servers is not None else load_registry()
    payload = {"version": 1, "source": "config/mcps/*.yaml", "servers": servers}
    return json.dumps(payload, indent=2) + "\n"


def render_anythingllm_from_servers(servers: list[dict[str, Any]]) -> str:
    entries: dict[str, dict[str, str]] = {}
    for server in servers:
        transport = server["transport"]
        if transport not in {"streamable-http", "sse"}:
            continue
        entries[server["name"]] = {
            "type": "streamable" if transport == "streamable-http" else "sse",
            "url": server["url"],
        }
    return json.dumps({"mcpServers": entries}, indent=2) + "\n"


def render_anythingllm(source: Path = MANIFEST) -> str:
    manifest = json.loads(source.read_text(encoding="utf-8"))
    return render_anythingllm_from_servers(list(manifest["servers"]))


def render_dynamic_anythingllm() -> str:
    """Keep AnythingLLM's native MCP list empty in V2.

    The Agent API owns progressive loading. The old direct config remains
    available for an explicit V1-style fallback.
    """

    return '{\n  "mcpServers": {}\n}\n'


def write_if_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    servers = load_registry()
    expected = {
        MANIFEST: render_manifest(servers),
        ANYTHINGLLM_DIRECT: render_anythingllm_from_servers(servers),
        ANYTHINGLLM_DYNAMIC: render_dynamic_anythingllm(),
    }
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, content in expected.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("out of date: " + ", ".join(stale))
            return 1
        print("MCP runtime configuration is current")
        return 0

    for path, content in expected.items():
        write_if_changed(path, content)
        print(f"generated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
