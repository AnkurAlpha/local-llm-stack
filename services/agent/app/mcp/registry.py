from __future__ import annotations

import json
from pathlib import Path

from .models import MCPService


class MCPRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def services(self) -> list[MCPService]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("unsupported MCP manifest version")
        result: list[MCPService] = []
        for item in payload.get("servers", []):
            result.append(
                MCPService(
                    name=str(item["name"]),
                    category=str(item["category"]),
                    transport=str(item["transport"]),
                    url=str(item["url"]),
                    metadata=dict(item),
                )
            )
        return result

    def get(self, name: str) -> MCPService:
        matches = [service for service in self.services() if service.name == name]
        if not matches:
            raise KeyError(name)
        return matches[0]
