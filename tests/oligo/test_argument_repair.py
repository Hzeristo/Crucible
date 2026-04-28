# crucible_core/tests/oligo/test_argument_repair.py
"""TP.3: conservative tool argument JSON repair."""
from __future__ import annotations

import json

import pytest

from src.oligo.core.agent import ChimeraAgent
from src.oligo.core.tool_protocol import (
    attempt_argument_repair,
    parse_args_with_repair,
)


# --- attempt_argument_repair: strip_code_fence (2) ---


def test_repair_strip_markdown_fence_json_label() -> None:
    raw = '```json\n{"query": "hello"}\n```'
    fixed, r = attempt_argument_repair(raw)
    assert r == ["strip_code_fence"]
    assert fixed == '{"query": "hello"}'


def test_repair_strip_markdown_fence_no_lang() -> None:
    raw = '```\n{"k": 1}\n```'
    fixed, r = attempt_argument_repair(raw)
    assert "strip_code_fence" in r
    assert fixed == '{"k": 1}'


# --- single_to_double_quote (2) ---


def test_repair_single_quotes_object() -> None:
    raw = "{'query': 'only singles'}"
    fixed, r = attempt_argument_repair(raw)
    assert "single_to_double_quote" in r
    assert '"query"' in fixed


def test_repair_single_quotes_nested_keys() -> None:
    raw = "{'a': '1', 'b': '2'}"
    fixed, r = attempt_argument_repair(raw)
    assert "single_to_double_quote" in r
    data, rep = parse_args_with_repair(raw)
    assert data == {"a": "1", "b": "2"}
    assert rep


# --- trailing_comma (2) ---


def test_repair_trailing_comma_object() -> None:
    raw = '{"query": "x",}'
    fixed, r = attempt_argument_repair(raw)
    assert "trailing_comma" in r
    assert json.loads(fixed) == {"query": "x"}


def test_repair_trailing_comma_two_keys() -> None:
    raw = '{"a": 1, "b": 2,}'
    fixed, r = attempt_argument_repair(raw)
    assert "trailing_comma" in r
    assert json.loads(fixed) == {"a": 1, "b": 2}


# --- wrap_braces (2) ---


def test_repair_wrap_missing_outer_braces() -> None:
    raw = '"query": "wrapped"'
    fixed, r = attempt_argument_repair(raw)
    assert "wrap_braces" in r
    assert json.loads(fixed) == {"query": "wrapped"}


def test_repair_wrap_with_inner_colon() -> None:
    raw = '"q": "a:b"'
    fixed, r = attempt_argument_repair(raw)
    assert "wrap_braces" in r
    assert json.loads(fixed) == {"q": "a:b"}


# --- smart_quotes (2) ---


def test_repair_smart_double_quotes() -> None:
    left, right = "\u201c", "\u201d"
    raw = f"{{{left}query{right}: {left}hi{right}}}"
    fixed, r = attempt_argument_repair(raw)
    assert "smart_quotes" in r
    assert json.loads(fixed) == {"query": "hi"}


def test_repair_smart_quotes_with_other_repairs() -> None:
    L, R = "\u201c", "\u201d"
    inner = "{" + f"{L}q{R}: {L}x{R},}}"
    raw = f"```\n{inner}\n```"
    data, repairs = parse_args_with_repair(raw)
    assert data == {"q": "x"}
    assert "strip_code_fence" in repairs
    assert "trailing_comma" in repairs
    assert "smart_quotes" in repairs


# --- parse_args_with_repair ---


def test_parse_args_with_repair_no_repair_needed() -> None:
    d, r = parse_args_with_repair(' {"foo": 1} ')
    assert d == {"foo": 1}
    assert r == []


def test_parse_args_with_repair_top_level_string_coercion() -> None:
    d, r = parse_args_with_repair('"plain"')
    assert d == {"query": "plain"}
    assert r == []


def test_parse_args_with_repair_failure_raises() -> None:
    with pytest.raises(ValueError, match="Cannot parse args even with repair"):
        parse_args_with_repair("NOT_JSON {{{")


def test_attempt_argument_repair_failure_returns_empty_repairs() -> None:
    s, r = attempt_argument_repair("{broken")
    assert s == "{broken"
    assert r == []


# --- agent integration ---


def test_agent_applies_repair_and_sets_planned_repairs(mock_client) -> None:
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "hi"}],
        system_core="sys",
        skill_override=None,
        llm_client=mock_client(),
        allowed_tools=None,
    )
    probe = '<CMD:search_vault({\'query\': \'fixme\'})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].allowed is True
    assert planned[0].args == {"query": "fixme"}
    assert "single_to_double_quote" in planned[0].repairs_applied
