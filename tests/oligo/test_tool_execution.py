# crucible_core/tests/oligo/test_tool_execution.py
"""Tests for ChimeraAgent tool execution layer."""
from __future__ import annotations

import asyncio

from src.crucible.core.schemas import ExecutedToolResult, PlannedToolCall, ToolCallStatus
from src.oligo.core.agent import ChimeraAgent


def test_parse_tool_calls_extracts_single_tool(mock_client):
    """LLM output with one <CMD:search_vault(...)> parses correctly."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Find papers about RAG"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    probe = '<CMD:search_vault({"query": "RAG"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].tool_name == "search_vault"
    assert planned[0].args == {"query": "RAG"}
    assert planned[0].allowed is True
    assert planned[0].deny_reason is None


def test_parse_tool_calls_whitelist_denies_unlisted(mock_client):
    """Tool not in allowed_tools list is marked denied."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search web"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=["search_vault"],  # web_search NOT allowed
    )
    probe = '<CMD:web_search({"query": "latest AI news"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].tool_name == "web_search"
    assert planned[0].allowed is False
    assert "not allowed" in planned[0].deny_reason


def test_parse_tool_calls_multiple_tools_in_response(mock_client):
    """Response with multiple <CMD:...> tags parses all of them."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Compare approaches"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    probe = '<CMD:search_vault({"query": "RAG"})><CMD:web_search({"query": "RAG benchmark"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 2
    assert [p.tool_name for p in planned] == ["search_vault", "web_search"]


def test_parse_tool_calls_invalid_json_args_uses_empty_dict(mock_client):
    """Invalid JSON in args falls back to empty dict (execute step re-validates)."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    probe = '<CMD:search_vault(NOT_JSON)>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].args == {}
    assert planned[0].allowed is True


def test_parse_tool_calls_no_cmds_returns_empty(mock_client):
    """Response with no <CMD:...> returns empty list."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Hello"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    planned = agent._parse_tool_calls("Hello, how are you?")
    assert planned == []


# ---------------------------------------------------------------------------
# Tests for _execute_tool_calls
# ---------------------------------------------------------------------------


async def test_execute_tool_calls_denied_tools_materialized(mock_client):
    """Denied tools are materialized as ExecutedToolResult with DENIED status, no execution."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=["search_vault"],  # web_search denied
    )
    planned = [
        PlannedToolCall(
            id="call-001",
            tool_name="web_search",
            raw_args='{"query": "AI"}',
            args={"query": "AI"},
            allowed=False,
            deny_reason="Tool 'web_search' is not allowed under current skill.",
        )
    ]
    results = await agent._execute_tool_calls(planned)
    assert len(results) == 1
    assert results[0].status == ToolCallStatus.DENIED
    assert results[0].call_id == "call-001"
    assert "not allowed" in results[0].error_message


async def test_execute_tool_calls_unknown_tool_returns_error(mock_client):
    """Unknown tool name is caught at execution and returns ERROR status."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Do something"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    planned = [
        PlannedToolCall(
            id="call-002",
            tool_name="nonexistent_tool",
            raw_args="{}",
            args={},
            allowed=True,
            deny_reason=None,
        )
    ]
    results = await agent._execute_tool_calls(planned)
    assert len(results) == 1
    assert results[0].status == ToolCallStatus.ERROR
    assert "not recognized" in results[0].raw_result


async def test_execute_tool_calls_empty_list_returns_empty(mock_client):
    """Empty planned_calls list returns empty results."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Hello"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    results = await agent._execute_tool_calls([])
    assert results == []


# ---------------------------------------------------------------------------
# Tests for run_theater integration
# ---------------------------------------------------------------------------


class _MockVaultAdapter:
    """Minimal vault adapter for testing."""

    async def search_notes(self, query: str, top_k: int = 3) -> str:
        return f"[MockVault] Found 2 notes about: {query}"

    async def search_by_attribute(
        self, key: str, value: str, top_k: int = 5
    ) -> str:
        return f"[MockVault] Found notes with {key}={value}"


async def test_run_theater_with_tool_calls_executes_and_streams(mock_client):
    """Router returns tool call -> executor runs it -> final response streamed."""
    client = mock_client()
    client.probe_response = '<CMD:search_vault({"query": "Titans"})>'
    client.final_response = "Based on the vault search, Titans is a memory architecture."

    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Tell me about Titans paper"}],
        system_core="You are BB.",
        skill_override=None,
        llm_client=client,
        max_turns=3,
        allowed_tools=["search_vault"],
        vault=_MockVaultAdapter(),
    )

    chunks = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    assert client.probe_call_count == 1
    assert client.final_call_count == 1
    # Final response should appear in chunks
    content_chunks = [c for c in chunks if "Titans" in c or "memory" in c]
    assert len(content_chunks) > 0


async def test_run_theater_no_tool_passes_through_to_final_stream(mock_client):
    """When router returns PASS (no tools), direct to final stream."""
    client = mock_client()
    client.probe_response = "<PASS>"
    client.final_response = "Hello from the other side."

    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Hello BB"}],
        system_core="You are BB.",
        skill_override=None,
        llm_client=client,
        max_turns=3,
        allowed_tools=None,
        vault=None,
    )

    chunks = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    assert client.probe_call_count == 1
    assert client.final_call_count == 1
    full_output = "".join(chunks)
    assert "Hello from the other side" in full_output
