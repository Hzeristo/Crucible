"""Oligo Core: 剧场版 ReAct 引擎.

子模块（如 ``prompt_composer``）可独立导入；``ChimeraAgent`` 仅在按名访问时加载，避免
拉取未安装的 LLM 客户端依赖（如 ``openai``）。
``from src.oligo.core import ChimeraAgent`` 仍受支持。
"""

from __future__ import annotations

from typing import Any

__all__: list[str] = ["ChimeraAgent"]


def __getattr__(name: str) -> Any:
    if name == "ChimeraAgent":
        from src.oligo.core.agent import ChimeraAgent

        return ChimeraAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
