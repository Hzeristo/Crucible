"""
Oligo 工具注册表：将所有可调用工具集中管理，供 Chimera Agent 调度。
"""

from __future__ import annotations

from collections.abc import Callable

from src.oligo.tools.mock_vault import mock_search_vault

# 工具名 -> 异步可调用对象
TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "search_vault": mock_search_vault,
}
