from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import MCPService

CAPABILITY_DESCRIPTIONS = {
    "internet": "Search the internet and retrieve web content.",
    "browser": "Navigate and interact with web pages through a browser.",
    "documentation": "Look up current, library-specific developer documentation.",
    "database": "Query or update structured database data.",
    "time": "Get current time and convert between time zones.",
    "reasoning": "Break complex tasks into explicit reasoning steps.",
    "memory": "Search and manage persistent long-term memory.",
}

_KEYWORDS = {
    "internet": ("search", "fetch", "web", "url", "internet", "duckduckgo"),
    "browser": ("browser", "playwright", "navigate", "page", "click"),
    "documentation": ("documentation", "docs", "library", "context7"),
    "database": ("sql", "sqlite", "database", "query", "table"),
    "time": ("time", "timezone", "date"),
    "reasoning": ("thinking", "reason", "sequential"),
    "memory": ("memory", "chroma", "remember", "embedding"),
}
_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(slots=True)
class ServerRecord:
    service: MCPService
    status: str = "pending"
    tools: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    discovered_at: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.service.name,
            "category": self.service.category,
            "category_hint": list(self.service.category_hints),
            "description": self.service.description,
            "transport": self.service.transport,
            "endpoint": self.service.url,
            "status": self.status,
            "tool_count": len(self.tools),
            "error": self.error,
            "discovered_at": self.discovered_at,
        }


def normalize_tool(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("MCP returned a tool without a name")
    schema = raw.get("inputSchema") or raw.get("input_schema") or raw.get("parameters")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    result: dict[str, Any] = {
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "inputSchema": schema,
    }
    if isinstance(raw.get("annotations"), dict):
        result["annotations"] = dict(raw["annotations"])
    return result


def infer_hints(service: MCPService, tools: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    explicit = tuple(hint for hint in service.category_hints if hint)
    if explicit:
        return explicit
    text = " ".join(
        [
            service.name,
            service.description,
            *(f"{tool.get('name', '')} {tool.get('description', '')}" for tool in tools),
        ]
    ).lower()
    found = [hint for hint, words in _KEYWORDS.items() if any(word in text for word in words)]
    return tuple(found or ("general",))


def capability_payload(records: Iterable[ServerRecord]) -> dict[str, dict[str, Any]]:
    capabilities: dict[str, dict[str, Any]] = {}
    for record in records:
        hints = infer_hints(record.service, record.tools)
        for hint in hints:
            entry = capabilities.setdefault(
                hint,
                {
                    "id": hint,
                    "description": CAPABILITY_DESCRIPTIONS.get(
                        hint, f"Tools grouped under the {hint} capability."
                    ),
                    "providers": [],
                    "tools": [],
                    "available": False,
                    "errors": [],
                },
            )
            entry["providers"].append(
                {
                    "id": record.service.name,
                    "description": record.service.description,
                    "status": record.status,
                    "tool_count": len(record.tools),
                }
            )
            if record.healthy and record.tools:
                entry["available"] = True
            if record.error:
                entry["errors"].append({"provider": record.service.name, "error": record.error})
            for tool in record.tools:
                entry["tools"].append(
                    {
                        "provider": record.service.name,
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                    }
                )
    return dict(sorted(capabilities.items()))


def safe_tool_name(provider: str, tool_name: str) -> str:
    provider_part = _NAME_RE.sub("_", provider).strip("_") or "provider"
    tool_part = _NAME_RE.sub("_", tool_name).strip("_") or "tool"
    value = f"mcp__{provider_part}__{tool_part}"
    if len(value) <= 64:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"{value[:53]}_{digest}"


def openai_tool_definition(provider: str, tool: dict[str, Any]) -> dict[str, Any]:
    description = tool.get("description") or f"Call {tool['name']} on MCP provider {provider}."
    return {
        "type": "function",
        "function": {
            "name": safe_tool_name(provider, tool["name"]),
            "description": f"[{provider}] {description}",
            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
        },
    }


def schema_size(tools: Iterable[dict[str, Any]]) -> dict[str, int]:
    serialized = json.dumps(list(tools), separators=(",", ":"), ensure_ascii=False)
    chars = len(serialized)
    return {"characters": chars, "approx_tokens": (chars + 3) // 4}
