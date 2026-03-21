"""Oligo FastAPI application with lifespan-managed resources."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.crucible.llm_gateway import DeepSeekClient
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
    app.state.llm_client = DeepSeekClient()
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

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "oligo"}

    @app.post("/v1/agent/invoke")
    async def agent_invoke(request: Request, body: AgentInvokeRequest) -> StreamingResponse:
        """
        Agent 流式调用入口。

        从 lifespan 挂载的 llm_client 获取大模型客户端，组装 messages，
        实例化 ChimeraAgent 并返回 SSE 流式响应。全异步，不阻塞主事件循环。

        外层再包一层：把偶发逃逸的断连/取消也吃掉，避免 Starlette/Uvicorn 打印长栈。
        """
        client = request.app.state.llm_client
        agent = ChimeraAgent(messages=body.messages, llm_client=client)

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
