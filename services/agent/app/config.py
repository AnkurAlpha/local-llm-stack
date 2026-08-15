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
        )
