from __future__ import annotations

import json
import logging
from typing import Any

from .manager import MCPDiscoveryManager

logger = logging.getLogger(__name__)


class DynamicOrchestrator:
    """Run a compact skill-first tool loop for OpenAI-compatible requests."""

    def __init__(self, manager: MCPDiscoveryManager, provider: Any, max_steps: int = 8) -> None:
        self.manager = manager
        self.provider = provider
        self.max_steps = max(1, max_steps)

    @staticmethod
    def activation_tool(skills: list[dict[str, Any]]) -> dict[str, Any]:
        identifiers = [str(skill["id"]) for skill in skills]
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Exact ID of the skill to load.",
                }
            },
            "required": ["skill_id"],
            "additionalProperties": False,
        }
        if identifiers:
            parameters["properties"]["skill_id"]["enum"] = identifiers
        return {
            "type": "function",
            "function": {
                "name": "lmctl_activate_skill",
                "description": (
                    "Load the detailed MCP tool definitions for one available capability. "
                    "Use the exact skill ID from the LMCTL skill registry."
                ),
                "parameters": parameters,
            },
        }

    @staticmethod
    def _arguments(call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") or {}
        raw = function.get("arguments", {})
        if raw in (None, ""):
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        raise ValueError("tool arguments must be a JSON object")

    @staticmethod
    def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            return [call for call in calls if isinstance(call, dict)]
        legacy = message.get("function_call")
        if isinstance(legacy, dict):
            return [{"id": "legacy-call", "type": "function", "function": legacy}]
        return []

    @staticmethod
    def _tool_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return json.dumps({"error": str(value)}, ensure_ascii=False)

    @staticmethod
    def _with_registry_prompt(messages: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        result = [dict(message) for message in messages]
        if result and result[0].get("role") == "system":
            original = result[0].get("content") or ""
            result[0] = {**result[0], "content": f"{original}\n\n{prompt}".strip()}
        else:
            result.insert(0, {"role": "system", "content": prompt})
        return result

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        request_tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        history = self._with_registry_prompt(messages, self.manager.skill_prompt())
        activation = self.activation_tool(self.manager.skills())
        client_tools = [
            tool
            for tool in (request_tools or [])
            if isinstance(tool, dict)
            and (tool.get("function") or {}).get("name") != "lmctl_activate_skill"
        ]
        active_skill_ids: set[str] = set()
        loaded_tools: list[dict[str, Any]] = []
        available_tools = [activation, *client_tools]

        for step in range(self.max_steps):
            response = await self.provider.chat(
                history,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=available_tools,
            )
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                break
            calls = self._tool_calls(message)
            if not calls:
                self.manager.record_active(active_skill_ids)
                response["lmctl"] = self._metrics(active_skill_ids, loaded_tools, step + 1)
                return response

            assistant_message = dict(message)
            if "content" not in assistant_message:
                assistant_message["content"] = ""
            history.append({"role": "assistant", **assistant_message})
            for call in calls:
                call_id = str(call.get("id") or f"lmctl-call-{step}")
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                try:
                    arguments = self._arguments(call)
                    if name == "lmctl_activate_skill":
                        skill_id = str(arguments.get("skill_id", ""))
                        activation_result = self.manager.skill_activation(skill_id)
                        active_skill_ids.add(skill_id)
                        loaded_tools.extend(activation_result["tools"])
                        loaded_tools = self._unique_tools(loaded_tools)
                        result: Any = {
                            "activated_skill": activation_result["skill"],
                            "instructions": activation_result["instructions"],
                            "loaded_tools": [
                                tool["function"]["name"] for tool in activation_result["tools"]
                            ],
                        }
                    elif name in {tool["function"]["name"] for tool in loaded_tools}:
                        result = await self.manager.call_tool(name, arguments)
                    else:
                        result = {
                            "error": f"Tool '{name}' is not active. Activate its skill first."
                        }
                except KeyError:
                    result = {"error": f"Unknown skill or MCP tool: {name}"}
                except Exception as exc:
                    logger.exception("Dynamic MCP tool call failed")
                    result = {"error": f"MCP call failed: {type(exc).__name__}"}
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": self._tool_content(result),
                    }
                )

            available_tools = [activation, *client_tools, *self._unique_tools(loaded_tools)]

        self.manager.record_active(active_skill_ids)
        response["lmctl"] = self._metrics(active_skill_ids, loaded_tools, self.max_steps)
        response["lmctl"]["warning"] = "dynamic tool loop reached the configured step limit"
        return response

    def _metrics(
        self,
        active_skill_ids: set[str],
        loaded_tools: list[dict[str, Any]],
        steps: int,
    ) -> dict[str, Any]:
        return {
            "active_skills": sorted(active_skill_ids),
            "loaded_tool_count": len(loaded_tools),
            "steps": steps,
            "context": self.manager.metrics(loaded_tools),
        }

    @staticmethod
    def _unique_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in tools:
            name = str((tool.get("function") or {}).get("name", ""))
            if name and name not in seen:
                seen.add(name)
                result.append(tool)
        return result
