# crucible_core/tests/oligo/test_tool_execution.py
"""Tests for ChimeraAgent tool execution layer."""
from __future__ import annotations

from src.crucible.core.schemas import ExecutedToolResult, PlannedToolCall, ToolCallStatus
from src.oligo.core.agent import (
    ChimeraAgent,
    _is_router_pass_or_trivial,
    _strip_markdown_code_for_cmd_extraction,
    _strip_router_dsl_for_backfill,
)
from src.oligo.tools.vault_tools import set_vault_adapter


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


def test_parse_tool_calls_unknown_tool_denied_at_parse(mock_client):
    """Hallucinated tool names are denied before execution with registry hint."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Read a file"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    probe = '<CMD:read_vault_file({"path": "note.md"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].tool_name == "read_vault_file"
    assert planned[0].allowed is False
    dr = planned[0].deny_reason or ""
    assert "not a registered tool" in dr
    assert "Available:" in dr


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


def test_parse_tool_calls_invalid_json_args_denies_call(mock_client):
    """Invalid JSON in <CMD> args is denied at parse time (S0.4)."""
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
    assert planned[0].allowed is False
    assert "Invalid JSON" in (planned[0].deny_reason or "")


def test_parse_tool_calls_ignores_cmd_inside_fenced_code(mock_client):
    """CMD only inside ``` — not matched; outside optional real CMD still parses."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Teach me"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    only_fence = '```\n<CMD:search_vault({"query": "x"})>\n```\n'
    assert agent._parse_tool_calls(only_fence) == []
    inline_only = 'Use `<CMD:search_vault({"query": "z"})>` in docs.'
    assert agent._parse_tool_calls(inline_only) == []
    with_outside = only_fence + '<CMD:search_vault({"query": "y"})>'
    p2 = agent._parse_tool_calls(with_outside)
    assert len(p2) == 1
    assert p2[0].args.get("query") == "y"


def test_strip_router_dsl_for_backfill_removes_tags():
    """Backfill text drops CMD / PASS literals."""
    s = "See <CMD:search_vault({\"query\": \"a\"})> and <PASS> tail."
    t = _strip_router_dsl_for_backfill(s)
    assert "CMD" not in t
    assert "PASS" not in t
    assert "See" in t or "tail" in t


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


def test_is_router_pass_or_trivial():
    """Trivial / PASS responses do not backfill; longer prose does."""
    assert _is_router_pass_or_trivial("") is True
    assert _is_router_pass_or_trivial("   ") is True
    assert _is_router_pass_or_trivial("<PASS>") is True
    assert _is_router_pass_or_trivial("  <pass>  ") is True
    assert _is_router_pass_or_trivial("x" * 29) is True
    assert _is_router_pass_or_trivial("x" * 30) is False
    long_nl = "Here is a natural language answer that exceeds the size threshold."
    assert _is_router_pass_or_trivial(long_nl) is False


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

    async def query_graph(
        self,
        node_type: str | None = None,
        link_pattern: str | None = None,
        max_depth: int = 2,
    ) -> list[dict[str, str | list[str] | None]]:
        return [
            {
                "title": "Mock",
                "path": "/dev/null/Mock.md",
                "type": "thought",
                "links": ["Other"],
            }
        ]


async def test_run_theater_with_tool_calls_executes_and_streams(mock_client):
    """Router returns tool call -> executor runs it -> final response streamed."""
    client = mock_client()
    client.probe_response = '<CMD:search_vault({"query": "Titans"})>'
    client.final_response = "Based on the vault search, Titans is a memory architecture."

    set_vault_adapter(_MockVaultAdapter())
    try:
        agent = ChimeraAgent(
            raw_messages=[{"role": "user", "content": "Tell me about Titans paper"}],
            system_core="You are BB.",
            skill_override=None,
            llm_client=client,
            max_turns=3,
            allowed_tools=["search_vault"],
        )

        chunks = []
        async for chunk in agent.run_theater():
            chunks.append(chunk)
    finally:
        set_vault_adapter(None)

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
    )

    chunks = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    assert client.probe_call_count == 1
    assert client.final_call_count == 1
    full_output = "".join(chunks)
    assert "Hello from the other side" in full_output


async def test_run_theater_natural_language_probe_backfilled_for_final(mock_client):
    """Long router response without <CMD> is appended as assistant for Final context."""
    draft = "Router draft: here is a summary that is long enough to backfill."
    assert not _is_router_pass_or_trivial(draft)
    client = mock_client()
    client.probe_response = draft
    client.final_response = "Polished persona answer."

    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "What is the note?"}],
        system_core="You are BB.",
        skill_override=None,
        llm_client=client,
        max_turns=3,
        allowed_tools=None,
    )

    chunks: list[str] = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    assert client.probe_call_count == 1
    assert client.final_call_count == 1
    # Final call must see the backfilled draft in messages
    final_call_messages = client.calls[1]
    contents = " ".join(
        (m.get("content") or "") for m in final_call_messages if m.get("role") != "system"
    )
    assert draft in contents
    assert "Polished persona answer" in "".join(chunks)
