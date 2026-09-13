from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import MCPService


def _hints(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, list) else str(value).split(",")
    return tuple(str(item).strip().lower() for item in values if str(item).strip())


class MCPRegistry:
    """Read the generated runtime view of the declarative MCP registry."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _payload(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") not in {1, 2}:
            raise ValueError("unsupported MCP manifest version")
        return payload

    def services(self) -> list[MCPService]:
        result: list[MCPService] = []
        for item in self._payload().get("servers", []):
            if not bool(item.get("enabled", True)):
                continue
            result.append(
                MCPService(
                    name=str(item["name"]),
                    category=str(item.get("category", "general")),
                    transport=str(item["transport"]),
                    url=str(item["url"]),
                    metadata=dict(item),
                    category_hints=_hints(item.get("category_hint")),
                    description=str(item.get("description", "")),
                    enabled=True,
                )
            )
        return result

    def get(self, name: str) -> MCPService:
        matches = [service for service in self.services() if service.name == name]
        if not matches:
            raise KeyError(name)
        return matches[0]
