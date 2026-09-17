from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .api_models import ChatRequest, ChatResponse
from .config import Settings
from .logconfig import configure_logging
from .mcp import (
    ActivityStore,
    ActivityTrace,
    DynamicOrchestrator,
    MCPClient,
    MCPDiscoveryManager,
    MCPRegistry,
)
from .model_registry import LocalModelRegistry
from .providers import ChatProvider, LlamaCppProvider
from .providers.errors import provider_error_detail

logger = logging.getLogger(__name__)


def _sse(payload: Any) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _openai_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    payload.update(extra)
    return payload


def _stream_dynamic_completion(
    orchestrator: DynamicOrchestrator,
    messages: list[dict[str, Any]],
    temperature: float | None,
    max_tokens: int | None,
    request_tools: list[dict[str, Any]],
    model: str,
    activity_enabled: bool,
    activity_max_events: int,
    activity_store: ActivityStore,
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        trace = ActivityTrace(
            request_id=completion_id,
            max_events=activity_max_events,
            enabled=activity_enabled,
            listener=events.put_nowait,
        )
        task = asyncio.create_task(
            orchestrator.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                request_tools=request_tools,
                activity=trace,
            )
        )
        try:
            yield _sse(_openai_chunk(completion_id, created, model, {"role": "assistant"}))
            while True:
                if task.done() and events.empty():
                    break
                try:
                    event = await asyncio.wait_for(events.get(), timeout=0.25)
                except TimeoutError:
                    continue
                yield _sse(
                    _openai_chunk(
                        completion_id,
                        created,
                        model,
                        {"reasoning_content": f"{event['message']}\n\n"},
                        lmctl_activity=event,
                    )
                )

            try:
                raw = task.result()
            except Exception as exc:
                activity_store.record(trace)
                logger.exception("dynamic streaming chat failed")
                yield _sse(
                    {
                        "error": {
                            "message": f"dynamic chat failed: {provider_error_detail(exc)}",
                            "type": "server_error",
                        }
                    }
                )
                yield "data: [DONE]\n\n"
                return

            model_name = str(raw.get("model") or model)
            choices = raw.get("choices") or [{}]
            choice = choices[0] or {}
            message = choice.get("message") or {}
            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason") or "stop"
            activity_store.record(trace)
            yield _sse(
                _openai_chunk(
                    completion_id,
                    created,
                    model_name,
                    {"content": str(content)},
                    lmctl=raw.get("lmctl"),
                )
            )
            yield _sse(
                _openai_chunk(
                    completion_id,
                    created,
                    model_name,
                    {},
                    finish_reason=finish_reason,
                    lmctl=raw.get("lmctl"),
                )
            )
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(body(), media_type="text/event-stream")


def create_app(
    provider: ChatProvider | None = None,
    settings: Settings | None = None,
    mcp_client: MCPClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    supplied_provider = provider

    model_registry = LocalModelRegistry(settings.state_root)
    mcp_registry = MCPRegistry(settings.mcp_config_path)
    mcp_client = mcp_client or MCPClient(settings.mcp_discovery_timeout)
    discovery = MCPDiscoveryManager(
        mcp_registry,
        mcp_client,
        settings.skills_path,
        settings.mcp_state_path,
        settings.mcp_discovery_timeout,
        settings.mcp_discovery_retries,
        settings.mcp_discovery_retry_delay,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.provider = supplied_provider or LlamaCppProvider(
            settings.llama_base_url,
            settings.llama_model_alias,
            settings.request_timeout,
        )
        application.state.discovery = discovery
        try:
            await discovery.refresh()
        except Exception:
            logger.exception("MCP startup discovery failed; serving diagnostics with no MCP tools")
        application.state.orchestrator = DynamicOrchestrator(
            discovery,
            application.state.provider,
            max_steps=settings.dynamic_max_steps,
            activity_max_events=settings.activity_max_events,
            explanation_enabled=settings.explanation_enabled,
            tool_trace_preview_chars=settings.tool_trace_preview_chars,
        )
        try:
            yield
        finally:
            await application.state.provider.close()

    application = FastAPI(
        title="Local LLM Agent API",
        version="0.2.0",
        description="Local LLM gateway with dynamic MCP capabilities and progressive tool loading.",
        lifespan=lifespan,
    )
    application.state.provider = supplied_provider
    application.state.discovery = discovery
    application.state.activity = ActivityStore(settings.activity_history_size)

    def get_provider(request: Request) -> ChatProvider:
        active = request.app.state.provider
        if active is None:
            raise HTTPException(status_code=503, detail="provider is not initialized")
        return active

    def get_discovery(request: Request) -> MCPDiscoveryManager:
        return request.app.state.discovery

    @application.get("/health")
    async def health(active: ChatProvider = Depends(get_provider)) -> dict[str, object]:  # noqa: B008
        current = model_registry.current()
        ready = bool(current) and await active.health()
        return {
            "status": "ok" if ready else "degraded",
            "llama_ready": ready,
            "model_selected": bool(current),
            "current_model": current.get("model_id") if current else None,
            "mcp": discovery.status()["status"],
        }

    @application.get("/models")
    async def models() -> dict[str, object]:
        return {"models": model_registry.models(), "current": model_registry.current()}

    @application.get("/models/current")
    async def current_model() -> dict[str, object]:
        current = model_registry.current()
        if current is None:
            raise HTTPException(status_code=404, detail="no model selected")
        return current

    @application.get("/v1/models")
    async def openai_models(active: ChatProvider = Depends(get_provider)) -> dict[str, Any]:  # noqa: B008
        try:
            data = await active.models()
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            data = []
        if not data:
            data = [{"id": settings.llama_model_alias, "object": "model", "owned_by": "lmctl"}]
        return {"object": "list", "data": data}

    @application.post("/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        active: ChatProvider = Depends(get_provider),  # noqa: B008
    ) -> ChatResponse:
        if model_registry.current() is None:
            raise HTTPException(status_code=503, detail="no model selected; run ./llmctl use MODEL")
        try:
            raw = await active.chat(
                [message.model_dump() for message in payload.messages],
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
            choice = raw["choices"][0]
            return ChatResponse(
                model=str(raw.get("model", settings.llama_model_alias)),
                content=str(choice["message"].get("content") or ""),
                finish_reason=choice.get("finish_reason"),
                usage=raw.get("usage"),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            logger.exception("llama.cpp chat request failed")
            raise HTTPException(
                status_code=502, detail=f"llama.cpp request failed: {provider_error_detail(exc)}"
            ) from exc

    @application.post("/v1/chat/completions")
    async def openai_chat(
        request: Request,
        active: ChatProvider = Depends(get_provider),  # noqa: B008
        manager: MCPDiscoveryManager = Depends(get_discovery),  # noqa: B008
    ) -> Any:
        if model_registry.current() is None:
            raise HTTPException(status_code=503, detail="no model selected; run ./llmctl use MODEL")
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=422, detail="messages must be a non-empty list")
        request_tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
        orchestrator = request.app.state.orchestrator
        try:
            normalized_messages = [
                dict(message) for message in messages if isinstance(message, dict)
            ]
            if payload.get("stream") is True:
                return _stream_dynamic_completion(
                    orchestrator,
                    normalized_messages,
                    payload.get("temperature"),
                    payload.get("max_tokens"),
                    request_tools,
                    str(payload.get("model") or settings.llama_model_alias),
                    settings.activity_enabled,
                    settings.activity_max_events,
                    request.app.state.activity,
                )
            trace = ActivityTrace(
                max_events=settings.activity_max_events,
                enabled=settings.activity_enabled,
            )
            try:
                raw = await orchestrator.complete(
                    normalized_messages,
                    temperature=payload.get("temperature"),
                    max_tokens=payload.get("max_tokens"),
                    request_tools=request_tools,
                    activity=trace,
                )
            finally:
                request.app.state.activity.record(trace)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.exception("dynamic chat request failed")
            raise HTTPException(
                status_code=502, detail=f"dynamic chat failed: {provider_error_detail(exc)}"
            ) from exc
        model = str(raw.get("model", settings.llama_model_alias))
        return raw

    @application.get("/mcp/services")
    async def mcp_services(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, object]:  # noqa: B008
        return {
            "services": [
                {
                    "name": service.name,
                    "category": service.category,
                    "category_hint": list(service.category_hints),
                    "description": service.description,
                    "transport": service.transport,
                    "url": service.url,
                }
                for service in manager.services()
            ]
        }

    @application.get("/mcp/services/{name}/tools")
    async def service_tools(
        name: str,
        manager: MCPDiscoveryManager = Depends(get_discovery),  # noqa: B008
    ) -> dict[str, object]:
        try:
            service = manager.registry.get(name)
            tools = await manager.client.list_tools(service)
            return {"service": name, "tools": tools}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown MCP service: {name}") from exc
        except Exception as exc:
            logger.exception("MCP tool discovery failed")
            raise HTTPException(
                status_code=502, detail=f"MCP discovery failed: {exc.__class__.__name__}"
            ) from exc

    @application.get("/mcp/status")
    async def mcp_status(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        return manager.status()

    @application.get("/mcp/discovery")
    async def mcp_discovery(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        return manager.status()

    @application.post("/mcp/refresh")
    async def mcp_refresh(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        return await manager.refresh()

    @application.get("/mcp/capabilities")
    async def mcp_capabilities(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        return {"capabilities": manager.capabilities, "metrics": manager.metrics()}

    @application.get("/mcp/skills")
    async def mcp_skills(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        return {"skills": manager.skills(), "prompt": manager.skill_prompt(), "metrics": manager.metrics()}

    @application.get("/mcp/skills/{skill_id}")
    async def mcp_skill(
        skill_id: str,
        manager: MCPDiscoveryManager = Depends(get_discovery),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            return manager.skill_activation(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown skill: {skill_id}") from exc

    @application.post("/mcp/skills/{skill_id}/activate")
    async def activate_skill(
        skill_id: str,
        manager: MCPDiscoveryManager = Depends(get_discovery),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            result = manager.skill_activation(skill_id)
            manager.record_active({skill_id})
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown skill: {skill_id}") from exc

    @application.get("/mcp/tools")
    async def discovered_tools(
        include_schema: bool = False,
        manager: MCPDiscoveryManager = Depends(get_discovery),  # noqa: B008
    ) -> dict[str, Any]:
        return {"tools": manager.tools(include_schema), "metrics": manager.metrics()}

    @application.get("/mcp/tools/active")
    async def active_tools(manager: MCPDiscoveryManager = Depends(get_discovery)) -> dict[str, Any]:  # noqa: B008
        tools = manager.active_tools(include_schema=True)
        return {"tools": tools, "metrics": manager.metrics(tools)}

    @application.get("/mcp/activity")
    async def mcp_activity(request: Request) -> dict[str, Any]:
        return {
            "enabled": settings.activity_enabled,
            "explanation_enabled": settings.explanation_enabled,
            "tool_trace_preview_chars": settings.tool_trace_preview_chars,
            "latest": request.app.state.activity.latest(),
            "runs": request.app.state.activity.list(),
        }

    return application


app = create_app()
