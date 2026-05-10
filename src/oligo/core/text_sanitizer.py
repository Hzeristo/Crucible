"""统一文本清洗：推理标签 / 工具语法 / 历史消息（Oligo MW.3）。"""

from __future__ import annotations

import re
from typing import Any, Literal, overload

# 与 S0.4 ``<CMD:...>`` 一致，供 ``agent`` 再导出。
CMD_ARG_REGEX = re.compile(r"<CMD:([a-zA-Z0-9_]+)\((.*?)\)>", re.DOTALL)
_TOOLCALL_BLOCK_REGEX = re.compile(
    r"<tool_call[^>]*>.*?</tool_call>", re.IGNORECASE | re.DOTALL
)
_TOOLCALL_VOID_REGEX = re.compile(r"<tool_call[^>]*/>", re.IGNORECASE)

# 与历史 ``_strip_markdown_code_for_cmd_extraction`` 一致
_FENCED_BLOCK_REGEX = re.compile(
    r"```[a-zA-Z0-9_+.#*~-]*\s*\n?[\s\S]*?```",
    re.DOTALL,
)
_INLINE_CODE_REGEX = re.compile(r"`[^`\n]+`")

DEFAULT_MAX_ASSISTANT_CONTENT_CHARS: int = 8_000
_TRUNC_SUFFIX = "\n... [truncated in sanitize_messages_history]"

# 标签名拆字拼接：避免单独字面量经工具链写入时损坏，也避免在 ``"..."`` 中意外触发转义解释。
_REDACTED_THINKING_TAG = "redacted" + "_" + "thinking"


def _strip_cmd_pass_toolcall_raw(frag: str) -> str:
    if not frag:
        return ""
    t = _TOOLCALL_BLOCK_REGEX.sub("", frag)
    t = _TOOLCALL_VOID_REGEX.sub("", t)
    t = CMD_ARG_REGEX.sub("", t)
    t = re.sub(r"<CMD[^>]*>", "", t, flags=re.IGNORECASE)
    t = re.sub(r"<CMD[^>]*$", "", t, flags=re.IGNORECASE | re.MULTILINE)
    t = re.sub(r"<PASS\s*/?\s*>", "", t, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _span_merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    s = sorted(spans)
    if not s:
        return []
    m = [s[0]]
    for a, b in s[1:]:
        last_a, last_b = m[-1]
        if a > last_b:
            m.append((a, b))
        else:
            m[-1] = (last_a, max(last_b, b))
    return m


def _inlines_in_range(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    seg = text[lo:hi]
    r: list[tuple[int, int]] = []
    for m in _INLINE_CODE_REGEX.finditer(seg):
        r.append((lo + m.start(), lo + m.end()))
    return r


def _all_code_spans(text: str) -> list[tuple[int, int, Literal["fenced", "inline"]]]:
    n = len(text)
    if n == 0:
        return []
    fenced: list[tuple[int, int]] = []
    for m in _FENCED_BLOCK_REGEX.finditer(text):
        fenced.append((m.start(), m.end()))
    merged = _span_merge(fenced)
    inlines: list[tuple[int, int]] = []
    free_lo = 0
    for a, b in merged:
        inlines.extend(_inlines_in_range(text, free_lo, a))
        free_lo = b
    inlines.extend(_inlines_in_range(text, free_lo, n))
    out: list[tuple[int, int, Literal["fenced", "inline"]]] = [
        (a, b, "fenced") for a, b in merged
    ] + [(a, b, "inline") for a, b in inlines]
    return sorted(out, key=lambda x: x[0])


def _rebuild_protected(
    text: str,
    spans: list[tuple[int, int, Literal["fenced", "inline"]]],
    transform: Any,
) -> str:
    if not text:
        return ""
    if not spans:
        return transform(text)
    parts: list[str] = []
    cursor = 0
    for a, b, _ in spans:
        if cursor < a:
            parts.append(transform(text[cursor:a]))
        parts.append(text[a:b])
        cursor = b
    if cursor < len(text):
        parts.append(transform(text[cursor:]))
    return "".join(parts)


def _rebuild_stripping_outside_code(text: str, fn: Any) -> str:
    return _rebuild_protected(text, _all_code_spans(text), fn)


def _collect_declared_tool_call_ids(messages: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for m in messages:
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict) and isinstance(tc.get("id"), str) and tc["id"]:
                ids.add(tc["id"])
    return ids


def _drop_unpaired_tool_rows(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """丢弃 ``role=tool`` 但 ``tool_call_id`` 从未由前置 ``assistant.tool_calls`` 声明的行；丢弃无 id 的 tool 行。"""
    declared = _collect_declared_tool_call_ids(messages)
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "tool":
            tid = m.get("tool_call_id")
            if not isinstance(tid, str) or not tid or tid not in declared:
                continue
        out.append(m)
    return out


def _content_sanitize_str(content: str, role: str) -> str:
    c2 = TextSanitizer.strip_tool_syntax_in_visible(content)
    if role in ("assistant", "user", "tool", "system"):
        c2 = TextSanitizer.strip_reasoning_tags(c2)
    return c2


class TextSanitizer:
    """统一的文本清洗器, 三层职责清晰分离。"""

    @staticmethod
    def strip_reasoning_tags(text: str) -> str:
        if not text:
            return ""
        t = text
        # 分写两套配对，避免 ``(thinking|…)`` 下 ``</\1>`` 与部分引擎/边界行为冲突
        for tag in (_REDACTED_THINKING_TAG, "thinking"):
            pair = re.compile(
                rf"<{tag}[^>]*>[\s\S]*?</{tag}>",
                re.IGNORECASE | re.DOTALL,
            )
            for _ in range(64):
                n = pair.sub("", t)
                if n == t:
                    break
                t = n
        t = re.sub(
            fr"</(?:thinking|{_REDACTED_THINKING_TAG})\s*>",
            "",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            fr"<{_REDACTED_THINKING_TAG}[^>]*>[\s\S]*$",
            "",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"<thinking[^>]*>[\s\S]*$",
            "",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            fr"<(?:thinking|{_REDACTED_THINKING_TAG})[^>]*>",
            "",
            t,
            flags=re.IGNORECASE,
        )
        return t

    @staticmethod
    def strip_code_blocks_for_tool_matching(text: str) -> str:
        if not text:
            return ""
        out = _FENCED_BLOCK_REGEX.sub("", text)
        return _INLINE_CODE_REGEX.sub("", out)

    @staticmethod
    def strip_tool_syntax_in_visible(text: str) -> str:
        """去掉可见区的路由 DSL（``<CMD>`` / ``<PASS>`` / ``<tool_call>`` 等），**不**处理 fenced/行内代码内文本（S0.4 / TP.2）。"""
        if not text:
            return ""
        return _rebuild_stripping_outside_code(text, _strip_cmd_pass_toolcall_raw).strip()

    @staticmethod
    @overload
    def sanitize_messages_history(
        messages: list[dict[str, Any]],
        *,
        max_assistant_content_chars: int = ...,
    ) -> list[dict[str, Any]]: ...

    @staticmethod
    @overload
    def sanitize_messages_history(
        messages: list[Any],
        *,
        max_assistant_content_chars: int = ...,
    ) -> list[Any]: ...

    @staticmethod
    def sanitize_messages_history(  # type: ignore[misc, no-redef]
        messages: list[dict[str, Any]] | list[Any],
        *,
        max_assistant_content_chars: int = DEFAULT_MAX_ASSISTANT_CONTENT_CHARS,
    ) -> list[dict[str, Any]] | list[Any]:
        if not messages:
            return messages

        from src.crucible.core.schemas import ChatMessage

        is_chat = all(isinstance(m, ChatMessage) for m in messages)
        raw: list[dict[str, Any]] = [
            m.model_dump(exclude_none=True) if is_chat else dict(m) for m in messages
        ]  # type: ignore[arg-type, misc]

        raw = _drop_unpaired_tool_rows([dict(x) for x in raw])

        result: list[dict[str, Any]] = []
        for m in raw:
            mm = dict(m)
            role = str(mm.get("role", "user"))
            content = mm.get("content")
            if isinstance(content, str):
                c2 = _content_sanitize_str(content, role)
                if role == "assistant" and len(c2) > max_assistant_content_chars:
                    c2 = c2[:max_assistant_content_chars] + _TRUNC_SUFFIX
                mm["content"] = c2
            if is_chat:
                ke = {k: v for k, v in mm.items() if k in ChatMessage.model_fields}
                result.append(ke)  # type: ignore[assignment]
            else:
                result.append(mm)

        if is_chat:
            return [ChatMessage.model_validate(x) for x in result]  # type: ignore[return-value]
        return result
