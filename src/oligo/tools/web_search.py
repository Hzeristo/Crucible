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


async def web_search(query: str, **kwargs: Any) -> str:
    """Search the web for the given query using DuckDuckGo."""
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
        raw_results = await asyncio.wait_for(
            asyncio.to_thread(_fetch_results, q),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return f"[Tool Error]: web_search timed out after {TOOL_TIMEOUT_SECONDS}s."
    except Exception as exc:
        logger.warning("[web_search] search failed for query=%s: %s", q, exc)
        return f"[Tool Error]: web_search failed for '{q}': {exc}"

    if not raw_results:
        return f"[web_search] No results found for query: {q}"

    header = f"[WEB SEARCH] Query: {q}\n{'=' * 50}\n"
    return header + "\n\n".join(raw_results)


def _fetch_results(query: str) -> list[str]:
    """Blocking I/O — runs in thread pool via asyncio.to_thread."""
    results: list[str] = []
    with DDGS() as ddgs:
        for i, r in enumerate(ddgs.text(query, max_results=MAX_RESULTS), 1):
            title = r.get("title", "No title")
            url = r.get("href", "No URL")
            body = r.get("body", "")
            snippet = body[:300] + "..." if len(body) > 300 else body
            results.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")
    return results
