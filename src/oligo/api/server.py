"""Oligo FastAPI application with lifespan-managed resources."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.crucible.llm_gateway.client import OpenAICompatibleClient
from src.oligo.core.agent import (
    CLIENT_GONE_EXCEPTIONS,
    ChimeraAgent,
    _handle_client_gone,
    _looks_like_pipe_broken,
)
from src.oligo.domain.schemas import AgentInvokeRequest

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: init LLM client, graceful shutdown."""
    logger.info("[Oligo Core] Neural network engaged.")
    app.state.llm_client = OpenAICompatibleClient()
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
        """422 时把 Pydantic 校验明细打到日志，便于对照客户端载荷。"""
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
        """Health check endpoint."""
        return {"status": "ok", "service": "oligo"}

    @app.post("/v1/agent/invoke")
    async def agent_invoke(request: Request, body: AgentInvokeRequest) -> StreamingResponse:
        """
        Agent 流式调用入口。

        从 lifespan 挂载的 llm_client 获取客户端；``ChimeraAgent`` 在内部完成路由环与
        晚期人设绑定（不在此预拼 System 进 ``messages``）。

        外层再包一层：把偶发逃逸的断连/取消也吃掉，避免 Starlette/Uvicorn 打印长栈。
        """
        client = OpenAICompatibleClient(
            api_key=body.api_key if body.api_key else None,
            base_url=body.base_url if body.base_url else None,
            model=body.model_name if body.model_name else None,
        )
        if body.persona_id:
            logger.debug("[Oligo] invoke persona_id=%s", body.persona_id)

        agent = ChimeraAgent(
            raw_messages=body.messages,
            system_core=body.system_core,
            skill_override=body.skill_override,
            llm_client=client,
        )

        async def theater_stream():
            try:
                async for chunk in agent.run_theater():
                    yield chunk
            except CLIENT_GONE_EXCEPTIONS:
                _handle_client_gone()
                return
            except Exception as exc:
                if _looks_like_pipe_broken(exc):
                    _handle_client_gone()
                    return
                raise

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
