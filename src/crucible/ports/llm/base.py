"""LLM client protocol: single structural contract for Oligo and services."""

from __future__ import annotations

from typing import AsyncGenerator, Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    """LLM Client 的统一契约。"""

    async def generate_raw_text(self, messages: list[dict[str, str]]) -> str:
        """生成原始文本响应。"""
        ...

    async def stream_generate(
        self, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        """流式生成文本响应。"""
        ...

    async def generate_structured_data_async(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """生成结构化数据（异步）。"""
        ...
