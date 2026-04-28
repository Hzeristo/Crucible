"""Tests for TextSanitizer (MW.3)."""

from __future__ import annotations

from src.crucible.core.schemas import ChatMessage
from src.oligo.core.text_sanitizer import (
    DEFAULT_MAX_ASSISTANT_CONTENT_CHARS,
    TextSanitizer,
)


# --- Layer 1: reasoning ---


def test_strip_reasoning_simple_thinking() -> None:
    s = "先想<thinking>secret</thinking>后说：你好"
    assert TextSanitizer.strip_reasoning_tags(s) == "先想后说：你好"


def test_strip_reasoning_redacted_block() -> None:
    # 与 ``text_sanitizer._REDACTED_THINKING_TAG`` 一致：单段字面量可能在写入磁盘时被改坏
    rt = "redacted" + "_" + "thinking"
    s = f"A<{rt}>B</{rt}>C"
    assert TextSanitizer.strip_reasoning_tags(s) == "AC"


def test_strip_reasoning_nested_thinking() -> None:
    s = (
        "x<thinking>外<thinking>内</thinking>仍外</thinking>y"
    )
    out = TextSanitizer.strip_reasoning_tags(s)
    assert "<thinking>" not in out.lower()
    assert "内" not in out
    assert out.startswith("x") and out.endswith("y")


def test_strip_reasoning_unclosed_opening_truncates_rest() -> None:
    s = "ok<thinking>未闭合内容"
    out = TextSanitizer.strip_reasoning_tags(s)
    assert "未闭合" not in out
    assert "ok" in out


def test_strip_reasoning_orphan_closing_removed() -> None:
    s = "仅出现</thinking>尾"
    assert "</thinking>" not in TextSanitizer.strip_reasoning_tags(s)


def test_strip_reasoning_mixed_zh_en() -> None:
    s = "User 问 hi，<thinking>plan 步骤1</thinking>答：OK 好的"
    out = TextSanitizer.strip_reasoning_tags(s)
    assert "plan" not in out
    assert "答：OK" in out


# --- Layer 2: tool syntax in visible ---


def test_strip_tool_visible_removes_cmd_outside_code() -> None:
    s = 'Call <CMD:search_vault({"query": "a"})> now'
    out = TextSanitizer.strip_tool_syntax_in_visible(s)
    assert "<CMD" not in out
    assert "search_vault" not in out


def test_strip_tool_visible_removes_pass() -> None:
    s = "done <PASS> tail"
    assert "PASS" not in TextSanitizer.strip_tool_syntax_in_visible(s).upper()


def test_strip_tool_visible_removes_tool_call_xml() -> None:
    s = 'x<tool_call name="n"><args>{}</args></tool_call>y'
    out = TextSanitizer.strip_tool_syntax_in_visible(s)
    assert "tool_call" not in out.lower()


def test_strip_tool_visible_preserves_inside_fenced_block() -> None:
    s = "```\n<CMD:foo({\"a\":1})>\n```\nalso <CMD:bar({})>"
    out = TextSanitizer.strip_tool_syntax_in_visible(s)
    assert "<CMD:foo" in out
    assert "<CMD:bar" not in out


def test_strip_tool_visible_preserves_inline_code() -> None:
    s = "Use `<CMD:x({})>` in docs and real <CMD:y({})>"
    out = TextSanitizer.strip_tool_syntax_in_visible(s)
    assert "`<CMD:x" in out
    assert "<CMD:y" not in out


def test_strip_tool_visible_incomplete_cmd_at_eol() -> None:
    s = "bad <CMD\nstill"
    out = TextSanitizer.strip_tool_syntax_in_visible(s)
    assert "<CMD" not in out


def test_strip_code_for_matching_matches_s0() -> None:
    raw = "```\n<CMD:x({})\n```\n<CMD:z({})>"
    stripped = TextSanitizer.strip_code_blocks_for_tool_matching(raw)
    assert "<CMD:z" in stripped
    assert "<CMD:x" not in stripped


# --- Layer 3: history ---


def test_sanitize_truncates_long_assistant() -> None:
    long_a = "x" * (DEFAULT_MAX_ASSISTANT_CONTENT_CHARS + 100)
    msgs: list[dict] = [{"role": "assistant", "content": long_a}]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert isinstance(out, list)
    assert len(out[0]["content"]) <= DEFAULT_MAX_ASSISTANT_CONTENT_CHARS + 80
    assert "truncated" in out[0]["content"]


def test_sanitize_drops_tool_without_declared_id() -> None:
    msgs = [
        {"role": "user", "content": "u"},
        {
            "role": "tool",
            "content": "orphan",
            "tool_call_id": "call-99",
        },
    ]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert all(m.get("role") != "tool" for m in out)


def test_sanitize_keeps_tool_when_id_declared() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": "x",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f"}}],
        },
        {"role": "tool", "content": "r", "tool_call_id": "c1"},
    ]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert any(m.get("role") == "tool" for m in out)


def test_sanitize_strips_cmd_from_message_contents() -> None:
    msgs = [
        {
            "role": "user",
            "content": "hi <CMD:z({})>",
        }
    ]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert "<CMD" not in out[0]["content"]


def test_sanitize_accepts_chat_message_roundtrip() -> None:
    m = [
        ChatMessage(
            role="user",
            content="u<thinking>a</thinking>",
        )
    ]
    out = TextSanitizer.sanitize_messages_history(m)  # type: ignore[arg-type]
    assert isinstance(out[0], ChatMessage)
    assert "<thinking>" not in out[0].content


def test_sanitize_drops_malformed_tool_row() -> None:
    msgs: list[dict] = [
        {"role": "tool", "content": "no id"},
    ]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert out == []


def test_sanitize_strips_thinking_in_assistant() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": "<thinking>hidden</thinking>visible",
        }
    ]
    out = TextSanitizer.sanitize_messages_history(msgs)
    assert "visible" in out[0]["content"]
    assert "hidden" not in out[0]["content"]


