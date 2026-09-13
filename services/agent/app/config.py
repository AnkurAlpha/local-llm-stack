from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    llama_base_url: str
    llama_model_alias: str
    models_root: Path
    state_root: Path
    mcp_config_path: Path
    request_timeout: float
    log_level: str
    skills_path: Path = Path("/config/skills")
    mcp_state_path: Path = Path("/mcp-state/discovery.json")
    mcp_discovery_timeout: float = 20.0
    mcp_discovery_retries: int = 3
    mcp_discovery_retry_delay: float = 2.0
    dynamic_max_steps: int = 8

    @classmethod
    def from_env(cls) -> Settings:
        base_url = os.getenv("LLAMA_BASE_URL", "http://llama-cpp:8080/v1").rstrip("/")
        if not base_url.endswith("/v1"):
            raise ValueError("LLAMA_BASE_URL must end in /v1")
        return cls(
            llama_base_url=base_url,
            llama_model_alias=os.getenv("LLAMA_MODEL_ALIAS", "local-model"),
            models_root=Path(os.getenv("MODELS_ROOT", "/models")),
            state_root=Path(os.getenv("STATE_ROOT", "/state")),
            mcp_config_path=Path(os.getenv("MCP_CONFIG_PATH", "/config/mcp/servers.json")),
            request_timeout=float(os.getenv("AGENT_REQUEST_TIMEOUT", "600")),
            log_level=os.getenv("AGENT_LOG_LEVEL", "INFO").upper(),
            skills_path=Path(os.getenv("MCP_SKILLS_PATH", "/config/skills")),
            mcp_state_path=Path(os.getenv("MCP_STATE_PATH", "/mcp-state/discovery.json")),
            mcp_discovery_timeout=float(os.getenv("MCP_DISCOVERY_TIMEOUT", "20")),
            mcp_discovery_retries=max(0, int(os.getenv("MCP_DISCOVERY_RETRIES", "3"))),
            mcp_discovery_retry_delay=float(os.getenv("MCP_DISCOVERY_RETRY_DELAY", "2")),
            dynamic_max_steps=max(1, int(os.getenv("DYNAMIC_MAX_STEPS", "8"))),
        )
