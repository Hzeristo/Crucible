"""Oligo FastAPI application with lifespan-managed resources."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.crucible.bootstrap import (
    build_openai_client,
    build_openai_client_from_params,
    build_wash_client,
)
from src.crucible.core.config import load_config
from src.crucible.core.schemas import AgentInvokeRequest
from src.crucible.ports.vault.vault_read_adapter import VaultReadAdapter
from src.oligo.core.agent import ChimeraAgent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load settings, default LLM, and vault adapter once."""
    logger.info("[Oligo Core] Neural network engaged.")
    settings = load_config()
    app.state.settings = settings
    app.state.default_llm = build_openai_client(settings)
    app.state.wash_client = build_wash_client(settings)
    app.state.vault = VaultReadAdapter(settings)
    yield
    logger.info("[Oligo Core] Synapses disconnected.")


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
            allowed_tools=body.allowed_tools,
            vault=request.app.state.vault,
            agent_config=settings.oligo_agent,
        )

        async def theater_stream():
            async for chunk in agent.run_theater():
                yield chunk

        return StreamingResponse(
            theater_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()
