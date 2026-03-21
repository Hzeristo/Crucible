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
    Agent 调用传输协议契约。

    与 Astrocyte 前端发包格式契合，messages 为强类型，persona/skill 为可选的上下文注入变量。
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(
        ..., description="The conversation history and the current user prompt"
    )
    persona_id: str | None = Field(
        default=None, description="Optional persona ID for context injection"
    )
    skill_id: str | None = Field(
        default=None, description="Optional skill template ID for context injection"
    )