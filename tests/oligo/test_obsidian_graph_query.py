# crucible_core/tests/oligo/test_obsidian_graph_query.py
"""Lightweight tests for obsidian_graph_query."""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from src.oligo.tools import vault_tools as vt


class _GraphAdapterMock:
    """Minimal adapter: only ``query_graph`` is used by ``obsidian_graph_query``."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self._results = results if results is not None else []
        self.last_call: tuple[Any, ...] | None = None

    async def search_notes(self, query: str, top_k: int = 3) -> str:
        return ""

    async def search_by_attribute(
        self, key: str, value: str, top_k: int = 5
    ) -> str:
        return ""

    async def query_graph(
        self,
        node_type: str | None = None,
        link_pattern: str | None = None,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        self.last_call = (node_type, link_pattern, max_depth)
        return list(self._results)


@pytest.fixture(autouse=True)
def _reset_vault_adapter() -> Generator[None, None, None]:
    prev = vt._vault_adapter
    yield
    vt.set_vault_adapter(prev)


def test_obsidian_graph_query_adapter_not_set() -> None:
    """Uninitialized global adapter returns a clear error."""
    vt.set_vault_adapter(None)
    out = asyncio.run(vt.obsidian_graph_query())
    assert "Vault adapter not initialized" in out


def test_obsidian_graph_query_no_nodes() -> None:
    """Empty query_graph result yields [Graph Query] No nodes found and filter hints."""
    mock = _GraphAdapterMock(results=[])
    vt.set_vault_adapter(mock)
    out = asyncio.run(
        vt.obsidian_graph_query(node_type="decision", link_pattern="foo")
    )
    assert "[Graph Query] No nodes found" in out
    assert "type=" in out or "decision" in out
    assert mock.last_call is not None
    assert mock.last_call[0] == "decision"
    assert mock.last_call[1] == "foo"


def test_obsidian_graph_query_formats_nodes_and_links() -> None:
    mock = _GraphAdapterMock(
        results=[
            {
                "title": "NoteA",
                "type": "thought",
                "links": ["B", "C"],
            }
        ]
    )
    vt.set_vault_adapter(mock)
    out = asyncio.run(vt.obsidian_graph_query())
    assert "[Graph Query] Found 1 nodes" in out
    assert "NoteA" in out
    assert "thought" in out
    assert "Links:" in out
    assert "B" in out and "C" in out


def test_obsidian_graph_query_passes_max_depth() -> None:
    mock = _GraphAdapterMock(results=[])
    vt.set_vault_adapter(mock)
    asyncio.run(vt.obsidian_graph_query(max_depth=3))
    assert mock.last_call is not None
    assert mock.last_call[2] == 3


def test_obsidian_graph_query_truncates_beyond_ten() -> None:
    rows = [
        {
            "title": f"N{i}",
            "type": "k",
            "links": [],
        }
        for i in range(12)
    ]
    mock = _GraphAdapterMock(results=rows)
    vt.set_vault_adapter(mock)
    out = asyncio.run(vt.obsidian_graph_query())
    assert "Found 12 nodes" in out
    assert "more" in out.lower() or "…" in out or "10" in out
