from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capabilities import (
    ServerRecord,
    capability_payload,
    infer_hints,
    normalize_tool,
    openai_tool_definition,
    safe_tool_name,
    schema_size,
)
from .client import MCPClient
from .registry import MCPRegistry
from .skills import SkillCatalog

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MCPDiscoveryManager:
    """Discover MCP tools once at startup and expose compact skill metadata."""

    def __init__(
        self,
        registry: MCPRegistry,
        client: MCPClient,
        manual_skill_path: Path,
        state_path: Path,
        discovery_timeout: float = 20.0,
        discovery_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        self.registry = registry
        self.client = client
        self.manual_skill_path = manual_skill_path
        self.state_path = state_path
        self.discovery_timeout = discovery_timeout
        self.discovery_retries = max(0, discovery_retries)
        self.retry_delay = max(0.0, retry_delay)
        self.records: dict[str, ServerRecord] = {}
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.catalog = SkillCatalog({}, manual_skill_path)
        self.registry_error: str | None = None
        self.last_refresh: str | None = None
        self._tool_index: dict[str, tuple[str, dict[str, Any]]] = {}
        self._active_skill_ids: set[str] = set()
        self._refresh_lock = asyncio.Lock()

    async def _discover_one(self, service: Any) -> ServerRecord:
        record = ServerRecord(service=service)
        last_error: Exception | None = None
        for attempt in range(self.discovery_retries + 1):
            try:
                raw_tools = await asyncio.wait_for(
                    self.client.list_tools(service), timeout=self.discovery_timeout
                )
                record.tools = [normalize_tool(tool) for tool in raw_tools]
                record.status = "healthy"
                record.discovered_at = _now()
                logger.info(
                    "MCP discovery succeeded",
                    extra={"mcp_server": service.name, "tool_count": len(record.tools)},
                )
                return record
            except Exception as exc:
                last_error = exc
                if attempt < self.discovery_retries and self.retry_delay:
                    await asyncio.sleep(self.retry_delay)
        record.status = "unavailable"
        record.error = type(last_error).__name__ if last_error else "UnknownError"
        record.discovered_at = _now()
        logger.warning(
            "MCP discovery failed; continuing with remaining servers",
            extra={"mcp_server": service.name, "error": record.error},
        )
        return record

    async def refresh(self) -> dict[str, Any]:
        async with self._refresh_lock:
            self.registry_error = None
            try:
                services = self.registry.services()
            except Exception as exc:
                services = []
                self.registry_error = type(exc).__name__
                logger.exception("MCP registry could not be loaded")
            discovered = await asyncio.gather(*(self._discover_one(service) for service in services))
            self.records = {record.service.name: record for record in discovered}
            self.capabilities = capability_payload(self.records.values())
            self.catalog = SkillCatalog(self.capabilities, self.manual_skill_path)
            self._rebuild_tool_index()
            self._active_skill_ids = set()
            self.last_refresh = _now()
            self._write_state()
            return self.status()

    def _rebuild_tool_index(self) -> None:
        self._tool_index = {}
        for record in self.records.values():
            if not record.healthy:
                continue
            for tool in record.tools:
                name = safe_tool_name(record.service.name, tool["name"])
                self._tool_index[name] = (record.service.name, tool)

    def _write_state(self) -> None:
        payload = {
            "version": 1,
            "refreshed_at": self.last_refresh,
            "registry_error": self.registry_error,
            "servers": [record.payload() for record in self.records.values()],
            "capabilities": self.capabilities,
            "skills": self.catalog.skills(),
            "metrics": self.metrics(),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            logger.warning("MCP runtime state could not be written", exc_info=True)

    def status(self) -> dict[str, Any]:
        if self.registry_error:
            state = "error"
        elif any(record.status == "unavailable" for record in self.records.values()):
            state = "degraded"
        elif self.last_refresh:
            state = "ready"
        else:
            state = "starting"
        return {
            "status": state,
            "last_refresh": self.last_refresh,
            "registry_error": self.registry_error,
            "servers": [record.payload() for record in self.records.values()],
            "capabilities": self.capabilities,
            "skills": self.catalog.skills(),
            "metrics": self.metrics(),
        }

    def services(self) -> list[Any]:
        return [record.service for record in self.records.values()]

    def skills(self) -> list[dict[str, Any]]:
        return self.catalog.skills()

    def skill(self, identifier: str) -> dict[str, Any]:
        return self.catalog.get(identifier)

    def skill_prompt(self) -> str:
        return self.catalog.prompt()

    def skill_activation(self, identifier: str) -> dict[str, Any]:
        skill = self.catalog.get(identifier)
        capability = skill.get("capability")
        definitions = self.capability_tools(capability) if capability else []
        return {
            "skill": skill,
            "instructions": self.catalog.instructions(identifier),
            "tools": definitions,
        }

    def record_active(self, skill_ids: set[str]) -> None:
        self._active_skill_ids = set(skill_ids)

    def active_skill_ids(self) -> set[str]:
        return set(self._active_skill_ids)

    def capability_tools(self, capability_id: str | None) -> list[dict[str, Any]]:
        if not capability_id:
            return []
        definitions: list[dict[str, Any]] = []
        for record in self.records.values():
            if not record.healthy or capability_id not in infer_hints(record.service, record.tools):
                continue
            definitions.extend(openai_tool_definition(record.service.name, tool) for tool in record.tools)
        return definitions

    def all_openai_tools(self) -> list[dict[str, Any]]:
        return [openai_tool_definition(provider, tool) for provider, tool in self._tool_index.values()]

    def lookup_tool(self, function_name: str) -> tuple[Any, dict[str, Any]]:
        provider, tool = self._tool_index[function_name]
        return self.registry.get(provider), tool

    async def call_tool(self, function_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service, tool = self.lookup_tool(function_name)
        return await self.client.call_tool(service, tool["name"], arguments)

    def tools(self, include_schema: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for record in self.records.values():
            for tool in record.tools:
                item = {
                    "provider": record.service.name,
                    "provider_status": record.status,
                    "name": tool["name"],
                    "openai_name": safe_tool_name(record.service.name, tool["name"]),
                    "description": tool.get("description", ""),
                    "capabilities": list(infer_hints(record.service, [tool])),
                }
                if include_schema:
                    item["inputSchema"] = tool.get("inputSchema", {})
                result.append(item)
        return result

    def active_tools(
        self, skill_ids: set[str] | None = None, include_schema: bool = True
    ) -> list[dict[str, Any]]:
        skill_ids = self._active_skill_ids if skill_ids is None else skill_ids
        definitions: list[dict[str, Any]] = []
        for identifier in sorted(skill_ids):
            try:
                activation = self.skill_activation(identifier)
            except KeyError:
                continue
            definitions.extend(activation["tools"])
        if include_schema:
            return definitions
        return [
            {
                "name": item["function"]["name"],
                "description": item["function"].get("description", ""),
            }
            for item in definitions
        ]

    def metrics(self, active_tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        all_tools = self.all_openai_tools()
        active_tools = active_tools or []
        all_size = schema_size(all_tools)
        active_size = schema_size(active_tools)
        prompt_chars = len(self.skill_prompt())
        return {
            "configured_servers": len(self.records),
            "healthy_servers": sum(record.healthy for record in self.records.values()),
            "unhealthy_servers": sum(not record.healthy for record in self.records.values()),
            "discovered_tools": sum(len(record.tools) for record in self.records.values()),
            "available_capabilities": sum(
                bool(capability["available"]) for capability in self.capabilities.values()
            ),
            "available_skills": len(self.catalog.skills()),
            "all_tool_schema_characters": all_size["characters"],
            "all_tool_schema_approx_tokens": all_size["approx_tokens"],
            "active_tool_schema_characters": active_size["characters"],
            "active_tool_schema_approx_tokens": active_size["approx_tokens"],
            "compact_skill_prompt_characters": prompt_chars,
            "compact_skill_prompt_approx_tokens": (prompt_chars + 3) // 4,
        }
