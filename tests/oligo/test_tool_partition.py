# crucible_core/tests/oligo/test_tool_partition.py
"""TP.4: concurrency_safe batching for tool calls."""
from __future__ import annotations

from src.crucible.core.schemas import PlannedToolCall
from src.oligo.tools.registry import get_tool_registry, partition_tool_calls


def _p(tool_name: str) -> PlannedToolCall:
    return PlannedToolCall(
        id="id",
        tool_name=tool_name,
        raw_args="{}",
        args={},
        allowed=True,
        deny_reason=None,
        repairs_applied=[],
    )


def test_partition_all_safe_single_batch() -> None:
    reg = get_tool_registry()
    calls = [_p("search_vault"), _p("web_search"), _p("obsidian_graph_query")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 1
    assert [p.tool_name for p in batches[0]] == [
        "search_vault",
        "web_search",
        "obsidian_graph_query",
    ]


def test_partition_all_unsafe_each_own_batch() -> None:
    reg = get_tool_registry()
    calls = [_p("arxiv_miner"), _p("daily_paper_pipeline")]
    batches = partition_tool_calls(calls, reg)
    assert batches == [[calls[0]], [calls[1]]]


def test_partition_mixed_preserves_order() -> None:
    reg = get_tool_registry()
    calls = [
        _p("search_vault"),
        _p("web_search"),
        _p("arxiv_miner"),
        _p("obsidian_graph_query"),
        _p("daily_paper_pipeline"),
    ]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 4
    assert [p.tool_name for p in batches[0]] == ["search_vault", "web_search"]
    assert [p.tool_name for p in batches[1]] == ["arxiv_miner"]
    assert [p.tool_name for p in batches[2]] == ["obsidian_graph_query"]
    assert [p.tool_name for p in batches[3]] == ["daily_paper_pipeline"]


def test_partition_empty() -> None:
    assert partition_tool_calls([], get_tool_registry()) == []


def test_partition_single_tool() -> None:
    reg = get_tool_registry()
    c = [_p("check_task_status")]
    assert partition_tool_calls(c, reg) == [c]
