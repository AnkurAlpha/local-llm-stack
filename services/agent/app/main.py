from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from .api_models import ChatRequest, ChatResponse
from .config import Settings
from .logconfig import configure_logging
from .mcp import MCPClient, MCPRegistry
from .model_registry import LocalModelRegistry
from .providers import ChatProvider, LlamaCppProvider

logger = logging.getLogger(__name__)


def create_app(provider: ChatProvider | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    supplied_provider = provider

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.provider = supplied_provider or LlamaCppProvider(
            settings.llama_base_url,
            settings.llama_model_alias,
            settings.request_timeout,
        )
        try:
            yield
        finally:
            await application.state.provider.close()

    application = FastAPI(
        title="Local LLM Agent API",
        version="0.1.0",
        description="Provider- and MCP-aware foundation for future specialized agents.",
        lifespan=lifespan,
    )
    application.state.provider = supplied_provider
    model_registry = LocalModelRegistry(settings.state_root)
    mcp_registry = MCPRegistry(settings.mcp_config_path)
    mcp_client = MCPClient()

    def get_provider(request: Request) -> ChatProvider:
        active = request.app.state.provider
        if active is None:
            raise HTTPException(status_code=503, detail="provider is not initialized")
        return active

    @application.get("/health")
    async def health(active: ChatProvider = Depends(get_provider)) -> dict[str, object]:  # noqa: B008
        current = model_registry.current()
        ready = bool(current) and await active.health()
        return {
            "status": "ok" if ready else "degraded",
            "llama_ready": ready,
            "model_selected": bool(current),
            "current_model": current.get("model_id") if current else None,
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
                content=str(choice["message"]["content"]),
                finish_reason=choice.get("finish_reason"),
                usage=raw.get("usage"),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            logger.exception("llama.cpp chat request failed")
            raise HTTPException(
                status_code=502, detail=f"llama.cpp request failed: {exc.__class__.__name__}"
            ) from exc

    @application.get("/mcp/services")
    async def mcp_services() -> dict[str, object]:
        return {
            "services": [
                {
                    "name": service.name,
                    "category": service.category,
                    "transport": service.transport,
                    "url": service.url,
                }
                for service in mcp_registry.services()
            ]
        }

    @application.get("/mcp/services/{name}/tools")
    async def mcp_tools(name: str) -> dict[str, object]:
        try:
            service = mcp_registry.get(name)
            tools = await mcp_client.list_tools(service)
            return {"service": name, "tools": tools}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown MCP service: {name}") from exc
        except Exception as exc:
            logger.exception("MCP tool discovery failed")
            raise HTTPException(
                status_code=502, detail=f"MCP discovery failed: {exc.__class__.__name__}"
            ) from exc

    return application


app = create_app()
