# crucible_core/tests/oligo/test_tool_protocol.py
"""TP.2: XML + CMD unified tool call parsing."""
from __future__ import annotations

import pytest

from src.oligo.core.agent import ChimeraAgent, _strip_markdown_code_for_cmd_extraction
from src.oligo.core.tool_protocol import (
    TOOL_CALL_XML_PATTERN,
    parse_tool_calls_cmd,
    parse_tool_calls_unified,
    parse_tool_calls_xml,
    planned_call_id_from_parsed,
)


def test_parse_single_xml_tool_call() -> None:
    text = """<tool_call name="search_vault">
  <args>{"query": "memory architecture"}</args>
</tool_call>"""
    xs = parse_tool_calls_xml(text)
    assert len(xs) == 1
    assert xs[0].tool_name == "search_vault"
    assert xs[0].raw_args.strip() == '{"query": "memory architecture"}'
    assert xs[0].source_format == "xml"
    assert xs[0].call_id is None


def test_parse_multiple_xml_tool_calls() -> None:
    text = (
        '<tool_call name="search_vault"><args>{"query": "a"}</args></tool_call>\n'
        '<tool_call name="web_search"><args>{"query": "b"}</args></tool_call>'
    )
    xs = parse_tool_calls_xml(text)
    assert len(xs) == 2
    assert [x.tool_name for x in xs] == ["search_vault", "web_search"]


def test_parse_xml_args_special_chars() -> None:
    raw_json = (
        '{"query": "中文与 newline\\n 和 \\"quotes\\"", "note": "line1\\nline2"}'
    )
    text = f'<tool_call name="search_vault"><args>{raw_json}</args></tool_call>'
    xs = parse_tool_calls_xml(text)
    assert len(xs) == 1
    assert "中文" in xs[0].raw_args
    assert "\\n" in xs[0].raw_args or "\n" in xs[0].raw_args


def test_parse_cmd_still_works() -> None:
    text = '<CMD:search_vault({"query": "RAG"})>'
    cs = parse_tool_calls_cmd(text)
    assert len(cs) == 1
    assert cs[0].tool_name == "search_vault"
    assert cs[0].source_format == "cmd"


def test_parse_xml_and_cmd_mixed_document_order() -> None:
    text = (
        '<tool_call name="search_vault"><args>{"query": "1"}</args></tool_call>'
        '<CMD:web_search({"query": "2"})>'
    )
    u = parse_tool_calls_unified(text)
    assert [p.tool_name for p in u] == ["search_vault", "web_search"]
    assert [p.source_format for p in u] == ["xml", "cmd"]

    text2 = (
        '<CMD:web_search({"query": "2"})>'
        '<tool_call name="search_vault"><args>{"query": "1"}</args></tool_call>'
    )
    u2 = parse_tool_calls_unified(text2)
    assert [p.tool_name for p in u2] == ["web_search", "search_vault"]


def test_malformed_xml_does_not_raise() -> None:
    # 缺闭合标签：正则不匹配，不产出 XML；合并路径仍可从后缀 CMD 解析
    text = '<tool_call name="broken"><args>{"q": 1}'
    assert parse_tool_calls_xml(text) == []
    merged = parse_tool_calls_unified(text + '<CMD:x({})>')
    assert len(merged) == 1
    assert merged[0].tool_name == "x"
    assert merged[0].source_format == "cmd"


def test_xml_in_markdown_fence_not_executed_after_strip(mock_client) -> None:
    fenced = """```text
<tool_call name="search_vault">
  <args>{"query": "x"}</args>
</tool_call>
```"""
    stripped = _strip_markdown_code_for_cmd_extraction(fenced)
    assert TOOL_CALL_XML_PATTERN.search(stripped) is None
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "hi"}],
        system_core="sys",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    planned = agent._parse_tool_calls(fenced)
    assert planned == []


def test_agent_xml_optional_id_and_timeout_ignored(mock_client) -> None:
    probe = """<tool_call name="search_vault" id="t1" timeout="10s">
  <args>{"query": "k"}</args>
</tool_call>"""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "hi"}],
        system_core="sys",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].id == "t1"
    assert planned[0].args == {"query": "k"}


def test_planned_call_id_fallback_uuid_short() -> None:
    from src.oligo.core.tool_protocol import ParsedToolCall

    pc = ParsedToolCall(
        tool_name="search_vault",
        raw_args="{}",
        call_id=None,
        source_format="xml",
    )
    a = planned_call_id_from_parsed(pc)
    b = planned_call_id_from_parsed(pc)
    assert len(a) == 12
    assert len(b) == 12


@pytest.mark.parametrize(
    "broken",
    [
        "<tool_call name=x>",
        "<tool_call>no name</tool_call>",
        "",
    ],
)
def test_parse_tool_calls_unified_never_raises(broken: str) -> None:
    out = parse_tool_calls_unified(broken)
    assert isinstance(out, list)
