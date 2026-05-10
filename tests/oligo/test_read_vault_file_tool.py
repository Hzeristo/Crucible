"""Tests for read_vault_file tool behavior."""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from src.oligo.tools import vault_tools as vt
from src.oligo.tools.registry import get_tool_registry


class _ReadAdapterMock:
    def __init__(self, content: str = "", exc: Exception | None = None) -> None:
        self._content = content
        self._exc = exc
        self.last_path: str | None = None

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
        return []

    def read_file(self, path: str) -> str:
        self.last_path = path
        if self._exc is not None:
            raise self._exc
        return self._content


@pytest.fixture(autouse=True)
def _reset_vault_adapter() -> Generator[None, None, None]:
    prev = vt._vault_adapter
    yield
    vt.set_vault_adapter(prev)


def test_read_vault_file_registered_as_safe_tool() -> None:
    reg = get_tool_registry()
    spec = reg.get_spec("read_vault_file")
    assert spec is not None
    assert spec.concurrency_safe is True
    assert spec.long_running is False


def test_read_vault_file_adapter_not_initialized() -> None:
    vt.set_vault_adapter(None)
    out = asyncio.run(vt.read_vault_file("00_Inbox/a.md"))
    assert "Vault adapter not initialized" in out


def test_read_vault_file_requires_non_empty_path() -> None:
    vt.set_vault_adapter(_ReadAdapterMock(content="x"))
    out = asyncio.run(vt.read_vault_file("   "))
    assert "requires a non-empty path" in out


def test_read_vault_file_returns_full_content_with_header() -> None:
    mock = _ReadAdapterMock(content="# Note Title\n\nBody")
    vt.set_vault_adapter(mock)
    out = asyncio.run(vt.read_vault_file("00_Inbox/note.md"))
    assert out.startswith("[File: 00_Inbox/note.md]\n\n")
    assert "# Note Title" in out
    assert "Body" in out
    assert mock.last_path == "00_Inbox/note.md"


def test_read_vault_file_not_found_error() -> None:
    vt.set_vault_adapter(_ReadAdapterMock(exc=FileNotFoundError("missing")))
    out = asyncio.run(vt.read_vault_file("00_Inbox/missing.md"))
    assert "file not found" in out


def test_read_vault_file_invalid_path_error() -> None:
    vt.set_vault_adapter(_ReadAdapterMock(exc=ValueError("path escapes vault_root")))
    out = asyncio.run(vt.read_vault_file("../outside.md"))
    assert "invalid path" in out
    assert "escapes" in out
