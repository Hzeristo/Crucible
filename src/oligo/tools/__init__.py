# crucible_core/src/oligo/tools/__init__.py
"""Oligo tool registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.oligo.tools.web_search import web_search

# Registry of available tools. Keys are tool names (matching <CMD:tool_name(...)>).
# Values must be async callables: fn(**dict[str, Any]) -> Awaitable[str]
TOOL_REGISTRY: dict[str, Callable[..., Awaitable[str]]] = {
    "web_search": web_search,
}
