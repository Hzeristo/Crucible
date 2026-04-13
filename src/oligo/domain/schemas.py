"""Oligo 核心领域模型：绝对类型安全，消灭裸 dict。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """
    对话轮次的唯一权威实体。

    严格禁止不可控脏数据流入，为 OpenAI Function Calling 预留扩展字段。
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"] = Field(
        ..., description="The author of this message."
    )
    content: str = Field(..., description="The textual content of the message.")
    tool_call_id: str | None = Field(
        default=None,
        description="Reserved for OpenAI Function Calling (tool result messages).",
    )
    name: str | None = Field(
        default=None,
        description="Reserved for OpenAI Function Calling (tool name).",
    )


class AgentInvokeRequest(BaseModel):
    """
    与 Astrocyte 物理载荷对齐的调用契约。

    人设与技能由 ``system_core`` / ``skill_override`` 承载；``ChimeraAgent`` 在引擎内做晚期绑定。
    ``allowed_tools`` 预留权限；``persona_id`` 不参与决策，仅便于日志关联。
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., description="LLM API key from gateway (may be empty if server defaults apply).")
    base_url: str = Field(..., description="Chat/completions API base URL from gateway.")
    model_name: str = Field(..., description="Model id from gateway.")
    persona_id: str | None = Field(
        default=None,
        description="Optional persona id for logging only; not used for routing decisions.",
    )
    system_core: str = Field(
        ...,
        description="The full system prompt payload fetched by Rust (persona baseline).",
    )
    skill_override: str | None = Field(default=None)
    allowed_tools: list[str] | None = Field(
        default=None,
        description="Reserved for future heavy agent tool allowlists.",
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="Clean user/assistant transcript and current turn (no gateway-prefixed system).",
    )