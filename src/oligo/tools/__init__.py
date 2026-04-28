# crucible_core/src/oligo/tools/__init__.py
"""Oligo tool registry."""

from __future__ import annotations

from typing import Any

from src.oligo.tools.miner_tools import (
    arxiv_miner,
    check_task_status,
    daily_paper_pipeline,
)
from src.oligo.tools.vault_tools import (
    obsidian_graph_query,
    search_vault,
    search_vault_attribute,
)
from src.oligo.tools.registry import get_tool_registry
from src.oligo.tools.web_search import web_search

__all__ = [
    "TOOL_REGISTRY",
    "arxiv_miner",
    "check_task_status",
    "daily_paper_pipeline",
    "obsidian_graph_query",
    "search_vault",
    "search_vault_attribute",
    "web_search",
]


def __getattr__(name: str) -> Any:
    if name == "TOOL_REGISTRY":
        reg = get_tool_registry()
        return {n: reg.get(n) for n in reg.registered_names()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"TOOL_REGISTRY"})
