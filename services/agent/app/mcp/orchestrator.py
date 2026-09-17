from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ..providers.errors import provider_error_detail
from .activity import ActivityTrace
from .manager import MCPDiscoveryManager

logger = logging.getLogger(__name__)

_SENSITIVE_FIELD = re.compile(
    r"api[_-]?key|access[_-]?token|auth(?:entication|orization)?|bearer|cookie|"
    r"credential|pass(?:word|wd)?|private[_-]?key|secret|session|token",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|x-api-key|api[_-]?key|access[_-]?token|token|secret|"
    r"password|passwd|cookie)\s*[:=]\s*([^\s,;]+)"
)


class DynamicOrchestrator:
    """Run a compact skill-first tool loop for OpenAI-compatible requests."""

    def __init__(
        self,
        manager: MCPDiscoveryManager,
        provider: Any,
        max_steps: int = 8,
        activity_max_events: int = 64,
        explanation_enabled: bool = True,
        tool_trace_preview_chars: int = 4000,
    ) -> None:
        self.manager = manager
        self.provider = provider
        self.max_steps = max(1, max_steps)
        self.activity_max_events = max(1, activity_max_events)
        self.explanation_enabled = explanation_enabled
        self.tool_trace_preview_chars = max(128, tool_trace_preview_chars)

    def _trace_message(self, detailed: str, compact: str) -> str:
        """Choose a detailed execution trace without using model private reasoning."""
        return detailed if self.explanation_enabled else compact

    @classmethod
    def _redact_trace_value(cls, value: Any, key: str | None = None) -> Any:
        """Make activity fields useful without publishing obvious credentials."""
        if key and _SENSITIVE_FIELD.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._redact_trace_value(item, str(item_key))
                for item_key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._redact_trace_value(item) for item in value]
        if isinstance(value, bytes):
            return f"<{len(value)} bytes>"
        if isinstance(value, str):
            return _SENSITIVE_TEXT.sub(r"\1: [REDACTED]", value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)

    def _trace_json(self, value: Any) -> tuple[str, bool]:
        """Render a bounded, redacted JSON preview for a user-visible trace."""
        redacted = self._redact_trace_value(value)
        try:
            rendered = json.dumps(redacted, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            rendered = json.dumps({"value": str(redacted)}, ensure_ascii=False, indent=2)
        if len(rendered) <= self.tool_trace_preview_chars:
            return rendered, False
        remaining = len(rendered) - self.tool_trace_preview_chars
        return (
            f"{rendered[: self.tool_trace_preview_chars]}\n"
            f"… [truncated {remaining} character(s); adjust LMCTL_TOOL_TRACE_PREVIEW_CHARS]",
            True,
        )

    def _trace_error_detail(self, exc: Exception) -> str:
        """Include a useful error message while applying trace redaction."""
        detail = provider_error_detail(exc)
        exception_message = str(exc).strip()
        if exception_message and exception_message not in detail:
            detail = f"{detail}: {exception_message}"
        return str(self._redact_trace_value(detail))

    @staticmethod
    def _mcp_request(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Describe the MCP SDK invocation using the standard tools/call shape."""
        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

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
        activity: ActivityTrace | None = None,
    ) -> dict[str, Any]:
        trace = activity or ActivityTrace(max_events=self.activity_max_events)
        trace.emit(
            "request_started",
            self._trace_message(
                "### LMCTL request started\n"
                "LMCTL is evaluating the request against the available skills. "
                f"**Available skills:** `{len(self.manager.skills())}`.\n"
                "The trace below records only observable skill and MCP operations; "
                "it does not include private model deliberation.",
                "LMCTL is evaluating the request against the available skills",
            ),
            available_skills=len(self.manager.skills()),
        )
        history = self._with_registry_prompt(messages, self.manager.skill_prompt())
        activation = self.activation_tool(self.manager.skills())
        client_tools = [
            tool
            for tool in (request_tools or [])
            if isinstance(tool, dict) and (tool.get("function") or {}).get("name") != "lmctl_activate_skill"
        ]
        active_skill_ids: set[str] = set()
        loaded_tools: list[dict[str, Any]] = []
        available_tools = [activation, *client_tools]

        for step in range(self.max_steps):
            try:
                response = await self.provider.chat(
                    history,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=available_tools,
                )
            except Exception as exc:
                trace.emit(
                    "request_failed",
                    f"LMCTL request failed: {provider_error_detail(exc)}",
                    error=type(exc).__name__,
                    step=step + 1,
                )
                raise
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                break
            calls = self._tool_calls(message)
            if not calls:
                if choice.get("finish_reason") == "length" and not message.get("content"):
                    notice = (
                        "The model reached its output token limit before producing an answer. "
                        "Increase the output token budget or try a shorter request."
                    )
                    message["content"] = notice
                    trace.emit("warning", notice, step=step + 1)
                trace.emit(
                    "response_generating",
                    self._trace_message(
                        "### Generating final response\n"
                        f"**Step:** `{step + 1}`\n"
                        "The model returned an answer without requesting another skill or MCP tool.",
                        "Generating final response",
                    ),
                    step=step + 1,
                )
                self.manager.record_active(active_skill_ids)
                response["lmctl"] = self._metrics(active_skill_ids, loaded_tools, step + 1, trace)
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
                        skill = activation_result["skill"]
                        providers = list(skill.get("providers") or [])
                        provider_text = ", ".join(providers)
                        activation_request = {
                            "function": "lmctl_activate_skill",
                            "arguments": self._redact_trace_value(arguments),
                        }
                        trace.emit(
                            "skill_activated",
                            self._trace_message(
                                f"### Activated skill: {skill_id}\n"
                                "**Model-requested function:** `lmctl_activate_skill`\n"
                                "**Arguments:**\n"
                                f"```json\n{self._trace_json(activation_request['arguments'])[0]}\n```\n"
                                + (
                                    f"**Providers:** `{provider_text}`"
                                    if provider_text
                                    else "**Providers:** none"
                                ),
                                f"Activated skill: {skill_id}"
                                + (f" (providers: {provider_text})" if provider_text else ""),
                            ),
                            skill_id=skill_id,
                            providers=providers,
                            skill_kind=skill.get("kind"),
                            activation_request=activation_request,
                        )
                        loaded_names = [
                            self._display_tool_name(tool["function"]["name"])
                            for tool in activation_result["tools"]
                        ]
                        trace.emit(
                            "tools_loaded",
                            self._trace_message(
                                f"### Loaded tools: {', '.join(loaded_names) or 'none'}\n"
                                f"**Skill:** `{skill_id}`\n"
                                "Only these discovered tool schemas are now active for this request.",
                                "Loaded tools: " + (", ".join(loaded_names) or "none"),
                            ),
                            skill_id=skill_id,
                            tools=loaded_names,
                        )
                        result: Any = {
                            "activated_skill": activation_result["skill"],
                            "instructions": activation_result["instructions"],
                            "loaded_tools": [tool["function"]["name"] for tool in activation_result["tools"]],
                        }
                    elif name in {tool["function"]["name"] for tool in loaded_tools}:
                        service, tool = self.manager.lookup_tool(name)
                        display_name = f"{service.name}.{tool['name']}"
                        provider_name = service.name
                        mcp_tool_name = tool["name"]
                        safe_arguments = self._redact_trace_value(arguments)
                        mcp_request = self._mcp_request(mcp_tool_name, safe_arguments)
                        request_preview, request_truncated = self._trace_json(mcp_request)
                        started = time.perf_counter()
                        trace.emit(
                            "tool_call_started",
                            self._trace_message(
                                f"### Calling tool: {display_name}\n"
                                f"**Step:** `{step + 1}` · **Call ID:** `{call_id}`\n"
                                f"**OpenAI function:** `{name}`\n"
                                f"**MCP endpoint:** `{service.url}` ({service.transport})\n"
                                "**MCP `tools/call` request:**\n"
                                f"```json\n{request_preview}\n```",
                                f"Calling tool: {display_name}",
                            ),
                            provider=provider_name,
                            tool=mcp_tool_name,
                            openai_tool=name,
                            call_id=call_id,
                            endpoint=service.url,
                            transport=service.transport,
                            arguments=safe_arguments,
                            mcp_request=mcp_request,
                            mcp_request_preview=request_preview,
                            mcp_request_truncated=request_truncated,
                            step=step + 1,
                        )
                        tool_exception: Exception | None = None
                        try:
                            result = await self.manager.call_tool(name, arguments)
                        except Exception as exc:
                            tool_exception = exc
                            logger.warning("Dynamic MCP tool call failed", exc_info=True)
                            result = {"error": f"MCP call failed: {type(exc).__name__}"}
                        tool_error = tool_exception is not None or (
                            isinstance(result, dict) and result.get("isError") is True
                        )
                        duration_ms = round((time.perf_counter() - started) * 1000, 1)
                        result_preview, result_truncated = self._trace_json(result)
                        result_format = "json" if not result_truncated else "text"
                        error_name = type(tool_exception).__name__ if tool_exception else None
                        error_detail = (
                            self._trace_error_detail(tool_exception) if tool_exception else None
                        )
                        status = (
                            f"call raised {error_name}"
                            if error_name
                            else "MCP returned isError=true"
                            if tool_error
                            else "completed"
                        )
                        error_lines = (
                            f"**Error:** `{error_name}`\n**Detail:** {error_detail}\n"
                            if error_name
                            else ""
                        )
                        trace.emit(
                            "tool_call_failed" if tool_error else "tool_call_completed",
                            self._trace_message(
                                f"### {'Tool failed' if tool_error else 'Tool completed'}: {display_name}\n"
                                f"**Step:** `{step + 1}` · **Duration:** `{duration_ms} ms`\n"
                                f"**Status:** {status}\n"
                                + error_lines
                                + "**MCP result preview:**\n"
                                + f"```{result_format}\n{result_preview}\n```",
                                f"Tool failed: {display_name} ({error_name or 'MCP returned isError=true'})"
                                if tool_error
                                else f"Tool completed: {display_name}",
                            ),
                            provider=provider_name,
                            tool=mcp_tool_name,
                            openai_tool=name,
                            call_id=call_id,
                            endpoint=service.url,
                            transport=service.transport,
                            duration_ms=duration_ms,
                            result_preview=result_preview,
                            result_preview_truncated=result_truncated,
                            result_is_error=tool_error,
                            error=error_name,
                            error_detail=error_detail,
                            step=step + 1,
                        )
                    else:
                        safe_arguments = self._redact_trace_value(arguments)
                        argument_preview, arguments_truncated = self._trace_json(safe_arguments)
                        trace.emit(
                            "tool_call_rejected",
                            self._trace_message(
                                f"### Rejected inactive tool: {name or 'unnamed tool'}\n"
                                "LMCTL did not send this request to an MCP server because the tool "
                                "was not activated by a skill.\n"
                                "**Requested arguments:**\n"
                                f"```json\n{argument_preview}\n```",
                                f"Rejected inactive tool: {name or 'unnamed tool'}",
                            ),
                            openai_tool=name,
                            call_id=call_id,
                            arguments=safe_arguments,
                            arguments_preview=argument_preview,
                            arguments_truncated=arguments_truncated,
                            reason="tool is not active; activate its skill first",
                            step=step + 1,
                        )
                        result = {"error": f"Tool '{name}' is not active. Activate its skill first."}
                except KeyError:
                    trace.emit(
                        "tool_call_rejected",
                        self._trace_message(
                            f"### Rejected unknown skill or tool: {name or 'unnamed tool'}\n"
                            "LMCTL could not find this item in the current dynamic registry, so no "
                            "MCP request was sent.",
                            f"Rejected unknown skill or MCP tool: {name or 'unnamed tool'}",
                        ),
                        openai_tool=name,
                        call_id=call_id,
                        reason="unknown skill or tool in current dynamic registry",
                        step=step + 1,
                    )
                    result = {"error": f"Unknown skill or MCP tool: {name}"}
                except (TypeError, ValueError) as exc:
                    error_detail = str(self._redact_trace_value(str(exc)))
                    trace.emit(
                        "tool_call_rejected",
                        self._trace_message(
                            f"### Rejected invalid tool request: {name or 'unnamed tool'}\n"
                            f"**Reason:** {error_detail}",
                            f"Rejected invalid tool request: {name or 'unnamed tool'}",
                        ),
                        openai_tool=name,
                        call_id=call_id,
                        reason="invalid tool arguments",
                        error=type(exc).__name__,
                        error_detail=error_detail,
                        step=step + 1,
                    )
                    result = {"error": f"Invalid MCP tool arguments: {type(exc).__name__}"}
                except Exception as exc:
                    logger.exception("Dynamic MCP tool call failed")
                    error_detail = self._trace_error_detail(exc)
                    trace.emit(
                        "tool_call_failed",
                        self._trace_message(
                            f"### Tool handling failed: {name or 'unnamed tool'}\n"
                            f"**Error:** `{type(exc).__name__}`\n"
                            f"**Detail:** {error_detail}",
                            f"Tool handling failed: {name or 'unnamed tool'} ({type(exc).__name__})",
                        ),
                        openai_tool=name,
                        call_id=call_id,
                        error=type(exc).__name__,
                        error_detail=error_detail,
                        step=step + 1,
                    )
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
        trace.emit(
            "warning",
            self._trace_message(
                "### Dynamic tool loop reached the configured step limit\n"
                f"**Limit:** `{self.max_steps}` model/tool round(s).\n"
                "The completed MCP calls are listed above. LMCTL stopped before asking the "
                "model for another round to keep the request bounded.",
                "Dynamic tool loop reached the configured step limit",
            ),
            step=self.max_steps,
        )
        # The previous response may contain only tool calls.  Those calls
        # were handled above; returning them leaves UI clients with no answer.
        response["choices"] = [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (
                        "I reached the configured tool step limit and could not complete this request. "
                        "Try a smaller task, or increase DYNAMIC_MAX_STEPS if more tool steps are needed."
                    ),
                },
            }
        ]
        response["lmctl"] = self._metrics(active_skill_ids, loaded_tools, self.max_steps, trace)
        response["lmctl"]["warning"] = "dynamic tool loop reached the configured step limit"
        return response

    def _metrics(
        self,
        active_skill_ids: set[str],
        loaded_tools: list[dict[str, Any]],
        steps: int,
        trace: ActivityTrace,
    ) -> dict[str, Any]:
        return {
            "active_skills": sorted(active_skill_ids),
            "loaded_tool_count": len(loaded_tools),
            "steps": steps,
            "explanation_mode": "tool_trace" if self.explanation_enabled else "compact_activity",
            "activity": trace.events(),
            "context": self.manager.metrics(loaded_tools),
        }

    @staticmethod
    def _display_tool_name(function_name: str) -> str:
        prefix = "mcp__"
        if function_name.startswith(prefix):
            value = function_name[len(prefix) :]
            provider, separator, tool = value.partition("__")
            if separator:
                return f"{provider}.{tool}"
        return function_name

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
