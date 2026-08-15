from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_llmctl_shell_syntax_and_help() -> None:
    syntax = subprocess.run(["bash", "-n", str(ROOT / "llmctl")], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run([str(ROOT / "llmctl"), "help"], capture_output=True, text=True, check=False)
    assert help_result.returncode == 0
    assert "download OWNER/REPO" in help_result.stdout
    assert "memory status" in help_result.stdout
    mcp_result = subprocess.run(
        [str(ROOT / "llmctl"), "mcp", "list"], capture_output=True, text=True, check=False
    )
    assert mcp_result.returncode == 0
    assert "chroma-memory" in mcp_result.stdout


def test_compose_yaml_has_required_services_and_network() -> None:
    payload = yaml.safe_load((ROOT / "compose.yml").read_text())
    required = {"anythingllm", "llama-cpp", "model-manager", "agent-api", "mcp-tools", "memory-mcp", "chroma"}
    assert required <= set(payload["services"])
    for name in required:
        assert "llm-network" in payload["services"][name]["networks"]
    assert payload["services"]["chroma"]["volumes"] == ["./data/memory/chroma:/data"]
    assert "ports" not in payload["services"]["chroma"]
    assert all("healthcheck" in payload["services"][name] for name in required)
    for name in {"mcp-tools", "memory-mcp", "chroma", "model-manager"}:
        assert "ports" not in payload["services"][name]
    assert payload["services"]["memory-mcp"]["depends_on"]["chroma"]["condition"] == "service_healthy"


def test_cuda_overlay_and_internal_service_endpoints() -> None:
    compose = yaml.safe_load((ROOT / "compose.yml").read_text())
    cuda = yaml.safe_load((ROOT / "compose.cuda.yml").read_text())
    assert cuda == {"services": {"llama-cpp": {"gpus": "all"}}}
    assert compose["services"]["agent-api"]["environment"]["LLAMA_BASE_URL"] == ("http://llama-cpp:8080/v1")
    assert compose["services"]["anythingllm"]["environment"]["GENERIC_OPEN_AI_BASE_PATH"] == (
        "http://llama-cpp:8080/v1"
    )
    assert compose["services"]["memory-mcp"]["environment"]["CHROMA_HOST"] == ("${CHROMA_HOST:-chroma}")
    for service in ("anythingllm", "llama-cpp", "agent-api"):
        assert all(str(port).startswith("127.0.0.1:") for port in compose["services"][service]["ports"])


def test_secret_is_only_given_to_model_manager() -> None:
    payload = yaml.safe_load((ROOT / "compose.yml").read_text())
    services_with_token = [
        name for name, service in payload["services"].items() if "HF_TOKEN" in service.get("environment", {})
    ]
    assert services_with_token == ["model-manager"]
    assert "HF_TOKEN=" not in (ROOT / "config" / "mcp" / "servers.json").read_text()
