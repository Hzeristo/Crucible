# crucible_core/src/oligo/tools/web_search.py
"""Web search tool using duckduckgo-search (no API key required)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None  # type: ignore

logger = logging.getLogger(__name__)

MAX_RESULTS = 3
TOOL_TIMEOUT_SECONDS = 30.0


def _sync_search(q: str) -> list[dict[str, Any]]:
    """Blocking DuckDuckGo I/O — must run in a thread pool, not on the event loop."""
    rows: list[dict[str, Any]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(q, max_results=MAX_RESULTS):
            rows.append(dict(r))
    return rows


def _format_hits(query: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"[web_search] No results found for query: {query}"
    lines: list[str] = []
    for i, r in enumerate(rows, 1):
        title = r.get("title", "No title")
        url = r.get("href", "No URL")
        body = r.get("body", "")
        snippet = body[:300] + "..." if len(body) > 300 else body
        lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")
    header = f"[WEB SEARCH] Query: {query}\n{'=' * 50}\n"
    return header + "\n\n".join(lines)


async def web_search(query: str, **kwargs: Any) -> str:
    """
    Search the web using DuckDuckGo (no API key; uses duckduckgo-search).

    Args:
        query: Search keywords.

    Returns:
        Top search results (up to 3) with titles, URLs, and short snippets; or
        a ``[Tool Error]`` / ``[Tool Timeout]`` line on failure.
    """
    if not query:
        return "[Tool Error]: web_search requires a non-empty query string."

    if DDGS is None:
        return (
            "[Tool Error]: web_search requires `duckduckgo-search` package. "
            "Install with: pip install duckduckgo-search"
        )

    q = str(query).strip()
    if not q:
        return "[Tool Error]: web_search requires a non-empty query string."

    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(_sync_search, q),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return "[Tool Timeout]: DuckDuckGo search exceeded 30s."
    except Exception as exc:
        logger.warning("[Tool] web_search failed for query=%s: %s", q, exc)
        return f"[Tool Error]: web_search failed for '{q}': {exc}"

    return _format_hits(q, rows)
