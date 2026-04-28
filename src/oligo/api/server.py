"""Oligo FastAPI application with lifespan-managed resources."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.crucible.bootstrap import (
    build_openai_client_from_model_config,
    build_openai_client_from_params,
)
from src.crucible.core.config import get_config
from src.crucible.core.schemas import AgentInvokeRequest
from src.crucible.core.platform import get_chimera_root
from src.crucible.services.metrics_service import MetricsService
from src.crucible.services.task_service import TaskService, set_task_service
from src.crucible.ports.vault.vault_read_adapter import VaultReadAdapter
from src.oligo.core.agent import CLIENT_GONE_EXCEPTIONS, ChimeraAgent
from src.oligo.tools.vault_tools import set_vault_adapter
from src.oligo.core.sse import sse_event

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load settings, Wash/Router clients (Working LLM 仍由每请求 build_openai_client_from_params 构建)。"""
    logger.info("[Oligo] Neural network engaged.")
    settings = get_config()
    logger.info(
        "[Oligo] Listen bind from config: host=%s port=%s (start_oligo.py passes these to uvicorn)",
        settings.oligo_host,
        settings.oligo_port,
    )
    app.state.settings = settings
    app.state.wash_client = build_openai_client_from_model_config(
        settings, settings.llm.wash, provider_name="Wash"
    )
    if settings.llm.router:
        app.state.router_client = build_openai_client_from_model_config(
            settings, settings.llm.router, provider_name="Router"
        )
    else:
        app.state.router_client = None
    app.state.vault = VaultReadAdapter(settings)
    set_vault_adapter(app.state.vault)
    task_service = TaskService(get_chimera_root() / "tasks")
    app.state.task_service = task_service
    set_task_service(task_service)
    app.state.metrics = MetricsService(get_chimera_root() / "metrics.json")
    yield
    set_vault_adapter(None)
    logger.info("[Oligo] Synapses disconnected.")


def create_app() -> FastAPI:
    """Factory for Oligo FastAPI application."""
    app = FastAPI(
        title="Oligo",
        description="Project Chimera Agent Hub - Industrial-grade streaming proxy",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def log_validation_errors(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning(
            "[Oligo] 422 Unprocessable Entity | %s %s | errors=%s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        body = getattr(exc, "body", None)
        if body:
            preview = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
            if len(preview) > 4000:
                preview = preview[:4000] + "…(truncated)"
            logger.warning("[Oligo] 422 request body preview: %s", preview)
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok", "service": "oligo"}

    @app.post("/v1/agent/invoke")
    async def agent_invoke(request: Request, body: AgentInvokeRequest) -> StreamingResponse:
        settings = request.app.state.settings
        client = build_openai_client_from_params(
            api_key=body.api_key if body.api_key else None,
            base_url=body.base_url if body.base_url else None,
            model=body.model_name if body.model_name else None,
            temperature=body.temperature,
            default_settings=settings,
        )
        if body.persona_id:
            logger.debug("[Oligo] invoke persona_id=%s", body.persona_id)

        agent = ChimeraAgent(
            raw_messages=body.messages,
            system_core=body.system_core,
            skill_override=body.skill_override,
            llm_client=client,
            wash_client=request.app.state.wash_client,
            router_client=request.app.state.router_client,
            allowed_tools=body.allowed_tools,
            agent_config=settings.oligo_agent,
            max_turns=settings.oligo.max_turns,
            persona=body.persona,
            authors_note=body.authors_note,
            metrics_service=request.app.state.metrics,
        )

        metrics: MetricsService = request.app.state.metrics
        skill_key = (body.skill_id or "").strip() or None
        # Oligo 当前未汇总 API usage；占位为 0，结构保留供后续接入。
        recorded_tokens = 0
        stream_outcome = {"success": True}

        async def theater_stream():
            async for chunk in agent.run_theater():
                yield chunk

        async def safe_theater_stream():
            try:
                async for chunk in theater_stream():
                    yield chunk
            except CLIENT_GONE_EXCEPTIONS:
                stream_outcome["success"] = False
                logger.warning("[Oligo] Client disconnected during generation")
                yield sse_event(
                    "bb-stream-done",
                    {"aborted": True, "reason": "client_gone"},
                )
            except Exception as e:
                stream_outcome["success"] = False
                logger.exception("[Oligo] Unexpected error")
                yield sse_event(
                    "bb-stream-done",
                    {"error": True, "message": str(e)[:200]},
                )

        async def instrumented_stream():
            start = time.perf_counter()
            transport_ok = True
            try:
                async for chunk in safe_theater_stream():
                    yield chunk
            except Exception:
                transport_ok = False
                raise
            finally:
                latency_ms = (time.perf_counter() - start) * 1000.0
                metrics.record_request(
                    success=transport_ok and stream_outcome["success"],
                    latency_ms=latency_ms,
                    tokens=recorded_tokens,
                    skill_id=skill_key,
                )

        return StreamingResponse(
            instrumented_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()
