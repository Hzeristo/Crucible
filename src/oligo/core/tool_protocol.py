"""双格式工具调用解析：XML ``<tool_call>`` 与旧版 ``<CMD:...>``（TP.2）；参数修复（TP.3）。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

from src.oligo.core.text_sanitizer import CMD_ARG_REGEX

# 与 S0.4 CMD 一致（单源：text_sanitizer.CMD_ARG_REGEX）
TOOL_CALL_CMD_PATTERN = CMD_ARG_REGEX

# 允许多行、属性；``<tool_call`` 后须空白再接属性
TOOL_CALL_XML_PATTERN = re.compile(
    r"<tool_call\s+([^>]*?)>(.*?)</tool_call>",
    re.DOTALL | re.IGNORECASE,
)

_ARGS_BODY_PATTERN = re.compile(r"<args\s*>([\s\S]*?)</args\s*>", re.IGNORECASE)
_NAME_ATTR_PATTERN = re.compile(
    r"""(?P<pre>[=\s]|^)\s*name\s*=\s*(?P<q>['"])(?P<name>.*?)(?P=q)""",
    re.IGNORECASE,
)
_ID_ATTR_PATTERN = re.compile(
    r"""(?P<pre>[=\s]|^)\s*id\s*=\s*(?P<q>['"])(?P<id>.*?)(?P=q)""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedToolCall:
    tool_name: str
    raw_args: str
    call_id: str | None
    source_format: Literal["xml", "cmd"]


def _xml_attr_name_id(attrs_str: str) -> tuple[str | None, str | None]:
    """从属性串解析 ``name`` / ``id``（ET 失败时的兜底）。"""
    name_m = _NAME_ATTR_PATTERN.search(attrs_str)
    name = name_m.group("name") if name_m else None
    id_m = _ID_ATTR_PATTERN.search(attrs_str)
    cid = id_m.group("id") if id_m else None
    return (name, cid)


def _xml_args_raw_from_body(body: str) -> str | None:
    m = _ARGS_BODY_PATTERN.search(body)
    if not m:
        return None
    return m.group(1).strip()


def _parse_one_xml_match(match: re.Match[str]) -> ParsedToolCall | None:
    attrs_str = match.group(1)
    body = match.group(2)
    name: str | None = None
    call_id: str | None = None
    args_text: str | None = None

    wrapped = f"<tool_call {attrs_str}>{body}</tool_call>"
    try:
        elem = ET.fromstring(wrapped)
        name = elem.get("name")
        raw_id = elem.get("id")
        call_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
        args_elem = elem.find("args")
        if args_elem is not None and args_elem.text is not None:
            args_text = args_elem.text.strip()
        elif args_elem is not None:
            args_text = ""
        else:
            args_text = None
    except (ET.ParseError, AttributeError):
        name, call_id = _xml_attr_name_id(attrs_str)
        args_text = _xml_args_raw_from_body(body)

    if not name or not str(name).strip():
        return None
    tool_name = str(name).strip()
    if args_text is None:
        args_text = "{}"
    return ParsedToolCall(
        tool_name=tool_name,
        raw_args=args_text,
        call_id=call_id,
        source_format="xml",
    )


def parse_tool_calls_xml(text: str) -> list[ParsedToolCall]:
    """解析 XML 格式。失败片段跳过，不抛异常。"""
    results: list[ParsedToolCall] = []
    for m in TOOL_CALL_XML_PATTERN.finditer(text):
        pt = _parse_one_xml_match(m)
        if pt is not None:
            results.append(pt)
    return results


def parse_tool_calls_cmd(text: str) -> list[ParsedToolCall]:
    """解析旧 CMD 格式（与 S0.4 同一正则）。"""
    results: list[ParsedToolCall] = []
    for m in TOOL_CALL_CMD_PATTERN.finditer(text):
        results.append(
            ParsedToolCall(
                tool_name=m.group(1),
                raw_args=m.group(2),
                call_id=None,
                source_format="cmd",
            )
        )
    return results


def parse_tool_calls_unified(text: str) -> list[ParsedToolCall]:
    """统一入口：在同一文本上匹配 XML 与 CMD，按出现位置合并（不去重）。"""
    tagged: list[tuple[int, ParsedToolCall]] = []
    for m in TOOL_CALL_XML_PATTERN.finditer(text):
        pt = _parse_one_xml_match(m)
        if pt is not None:
            tagged.append((m.start(), pt))
    for m in TOOL_CALL_CMD_PATTERN.finditer(text):
        tagged.append(
            (
                m.start(),
                ParsedToolCall(
                    tool_name=m.group(1),
                    raw_args=m.group(2),
                    call_id=None,
                    source_format="cmd",
                ),
            )
        )
    tagged.sort(key=lambda x: x[0])
    return [t[1] for t in tagged]


def extract_tool_syntax_for_history(stripped_text: str) -> str:
    """从已剥离 Markdown 代码的探针文本中，按文档顺序提取可审计的工具调用字面量。"""
    spans: list[tuple[int, str]] = []
    for m in TOOL_CALL_XML_PATTERN.finditer(stripped_text):
        spans.append((m.start(), m.group(0).strip()))
    for m in TOOL_CALL_CMD_PATTERN.finditer(stripped_text):
        spans.append((m.start(), m.group(0).strip()))
    spans.sort(key=lambda x: x[0])
    return "\n".join(s for _, s in spans)


def planned_call_id_from_parsed(pc: ParsedToolCall) -> str:
    """XML ``id`` 优先，否则短 UUID。"""
    if pc.call_id and str(pc.call_id).strip():
        s = str(pc.call_id).strip()
        return s[:128]
    return uuid.uuid4().hex[:12]


def attempt_argument_repair(raw_args: str) -> tuple[str, list[str]]:
    """
    尝试修复非法 JSON args（仅常见格式问题，不臆测语义）。

    Returns:
        (repaired_args, list_of_repairs_applied)；无法修复时 ``(raw_args, [])``。
    """
    repairs: list[str] = []
    candidate = raw_args.strip()

    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
        repairs.append("strip_code_fence")

    if "'" in candidate and '"' not in candidate:
        candidate = candidate.replace("'", '"')
        repairs.append("single_to_double_quote")

    candidate_new = re.sub(r",(\s*[}\]])", r"\1", candidate)
    if candidate_new != candidate:
        candidate = candidate_new
        repairs.append("trailing_comma")

    if (
        not candidate.startswith("{")
        and not candidate.startswith("[")
        and ":" in candidate
    ):
        candidate = "{" + candidate + "}"
        repairs.append("wrap_braces")

    smart_l = "\u201c"
    smart_r = "\u201d"
    candidate_new = candidate.replace(smart_l, '"').replace(smart_r, '"')
    if candidate_new != candidate:
        candidate = candidate_new
        repairs.append("smart_quotes")

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return raw_args, []
    if isinstance(data, (dict, str)):
        return candidate, repairs
    return raw_args, []


def _coerce_parsed_json_to_tool_args(data: Any) -> dict[str, Any]:
    """与历史 ``_parse_cmd_tool_args`` 一致：object 或宽容顶层 string → ``{\"query\": ...}``。"""
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        logger.warning(
            "[Oligo] Parser: LLM failed to output JSON Object. "
            "Coercing raw string to {'query': %r}",
            data,
        )
        return {"query": data}
    raise ValueError(f"Expected JSON object, got {type(data).__name__}")


def parse_args_with_repair(raw_args: str) -> tuple[dict[str, Any], list[str]]:
    """
    解析工具 args，必要时做保守格式修复。

    Returns:
        (parsed_dict, repairs_applied)；彻底失败时抛出 ``ValueError``。
    """
    s = raw_args.strip()
    try:
        data = json.loads(s)
        return _coerce_parsed_json_to_tool_args(data), []
    except json.JSONDecodeError:
        pass

    repaired, repairs = attempt_argument_repair(raw_args)
    if repairs:
        try:
            data = json.loads(repaired)
            return _coerce_parsed_json_to_tool_args(data), repairs
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"Cannot parse args even with repair: {raw_args[:100]!r}")
