from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_supervisor():
    path = ROOT / "services" / "mcp-tools" / "supervisor.py"
    spec = importlib.util.spec_from_file_location("mcp_supervisor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_anythingllm_config_is_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_mcp_configs.py"), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_has_exact_supplied_servers_and_internal_dns() -> None:
    payload = json.loads((ROOT / "config" / "mcp" / "servers.json").read_text())
    assert {item["name"] for item in payload["servers"]} == {
        "duckduckgo",
        "sequential-thinking",
        "fetch",
        "time",
        "sqlite",
        "context7",
        "playwright",
        "chroma-memory",
    }
    assert all(
        "localhost" not in item["url"] and "127.0.0.1" not in item["url"] for item in payload["servers"]
    )
    memory = next(item for item in payload["servers"] if item["name"] == "chroma-memory")
    assert memory["runtime"]["service"] == "memory-mcp"
    assert memory["storage"]["url"] == "http://chroma:8000"


def test_supervisor_uses_preinstalled_commands(monkeypatch) -> None:
    module = load_supervisor()
    monkeypatch.setenv("MCP_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setenv("MCP_RUNTIME_DIR", "/runtime")
    payload = json.loads((ROOT / "config" / "mcp" / "servers.json").read_text())
    for server in payload["servers"]:
        if server["category"] != "general":
            continue
        command = module.command_for(server)
        joined = " ".join(command)
        assert "npx -y" not in joined
        assert "uvx " not in joined
