# crucible_core/tests/oligo/test_tool_protocol_regression.py
"""TP.5：工具协议端到端回归（解析 / 混合格式 / 分批 / 修复）。"""
from __future__ import annotations

import pytest

from src.crucible.core.schemas import PlannedToolCall
from src.oligo.core.tool_protocol import parse_args_with_repair, parse_tool_calls_unified
from src.oligo.tools.registry import get_tool_registry, partition_tool_calls


def _plan(name: str) -> PlannedToolCall:
    return PlannedToolCall(
        id="id",
        tool_name=name,
        raw_args="{}",
        args={},
        allowed=True,
        deny_reason=None,
        repairs_applied=[],
    )


@pytest.mark.asyncio
async def test_xml_format_end_to_end() -> None:
    """模型用 XML 格式调用工具：统一解析链路可识别。"""
    response = (
        '<tool_call name="search_vault"><args>{"query":"test"}</args></tool_call>'
    )
    calls = parse_tool_calls_unified(response)
    assert len(calls) == 1
    assert calls[0].tool_name == "search_vault"
    assert calls[0].source_format == "xml"
    assert '"test"' in calls[0].raw_args or "test" in calls[0].raw_args


@pytest.mark.asyncio
async def test_cmd_format_still_works() -> None:
    """旧 CMD 格式仍正常。"""
    response = '<CMD:search_vault({"query":"test"})>'
    calls = parse_tool_calls_unified(response)
    assert len(calls) == 1
    assert calls[0].tool_name == "search_vault"
    assert calls[0].source_format == "cmd"


@pytest.mark.asyncio
async def test_mixed_format_in_one_response() -> None:
    """同一响应混用 XML 与 CMD，按出现顺序识别。"""
    response = (
        '<tool_call name="web_search"><args>{"query":"a"}</args></tool_call>'
        '<CMD:search_vault({"query":"b"})>'
    )
    calls = parse_tool_calls_unified(response)
    assert len(calls) == 2
    assert calls[0].tool_name == "web_search"
    assert calls[0].source_format == "xml"
    assert calls[1].tool_name == "search_vault"
    assert calls[1].source_format == "cmd"

    response2 = (
        '<CMD:search_vault({"query":"first"})>'
        '<tool_call name="web_search"><args>{"query":"second"}</args></tool_call>'
    )
    calls2 = parse_tool_calls_unified(response2)
    assert [c.tool_name for c in calls2] == ["search_vault", "web_search"]


@pytest.mark.asyncio
async def test_concurrent_safe_grouping() -> None:
    """concurrency_safe 工具与 unsafe 交替时分批正确。"""
    reg = get_tool_registry()
    calls = [
        _plan("search_vault"),
        _plan("web_search"),
        _plan("arxiv_miner"),
        _plan("check_task_status"),
    ]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 3
    assert [p.tool_name for p in batches[0]] == ["search_vault", "web_search"]
    assert [p.tool_name for p in batches[1]] == ["arxiv_miner"]
    assert [p.tool_name for p in batches[2]] == ["check_task_status"]


@pytest.mark.asyncio
async def test_arg_repair_smart_quotes() -> None:
    """智能引号经修复后可解析为对象。"""
    L, R = "\u201c", "\u201d"
    raw = "{" + f"{L}query{R}: {L}hi{R}" + "}"
    data, repairs = parse_args_with_repair(raw)
    assert data == {"query": "hi"}
    assert "smart_quotes" in repairs
