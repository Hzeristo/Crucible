# crucible_core/tests/oligo/test_web_search.py
"""Tests for web_search tool."""
from __future__ import annotations

import pytest

from src.oligo.tools.web_search import web_search


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_error():
    result = await web_search("")
    assert "Tool Error" in result
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_web_search_whitespace_only_returns_error():
    result = await web_search("   ")
    assert "Tool Error" in result


@pytest.mark.asyncio
async def test_web_search_library_missing_returns_install_hint():
    import src.oligo.tools.web_search as ws_module

    original_ddgs = ws_module.DDGS
    ws_module.DDGS = None  # type: ignore
    try:
        result = await web_search("test query")
        assert "Tool Error" in result
        assert "duckduckgo-search" in result
        assert "pip install" in result
    finally:
        ws_module.DDGS = original_ddgs


@pytest.mark.asyncio
async def test_web_search_none_query_returns_error():
    result = await web_search(None)  # type: ignore
    assert "Tool Error" in result
