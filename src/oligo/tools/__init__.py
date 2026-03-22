"""
Oligo 工具注册表：将所有可调用工具集中管理，供 Chimera Agent 调度。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.oligo.tools.obsidian_search import search_vault

# 工具名 -> 异步可调用对象
TOOL_REGISTRY: dict[str, Callable[..., Awaitable[str]]] = {
    "search_vault": search_vault,
}
