# crucible_core/tests/oligo/test_miner_tools.py
"""Tests for arxiv_miner, daily_paper_pipeline, and check_task_status.

本仓库未启用 pytest-asyncio 时，async 用例会被跳过；这里用 ``asyncio.run`` 保证在裸 pytest 下可执行。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from src.oligo.tools.miner_tools import arxiv_miner, check_task_status, daily_paper_pipeline


def test_arxiv_miner_returns_task_id():
    """arxiv_miner 应返回可追踪的任务标识（[Task Started] + created id + check_task_status 提示）。"""
    result = asyncio.run(arxiv_miner("memory architecture", max_results=1))
    assert "[Task Started]" in result
    assert "Arxiv mining task created" in result
    assert "check_task_status" in result


def test_check_task_status_invalid_id():
    """对未知 task_id 应返回明确错误/状态文案。"""
    result = asyncio.run(check_task_status("invalid_task_id"))
    assert "[Task" in result


def test_arxiv_miner_empty_query():
    """空查询必须拒绝，不得启动任务。"""
    result = asyncio.run(arxiv_miner(""))
    assert "[Tool Error]" in result


def test_daily_paper_pipeline_returns_task_id():
    """daily_paper_pipeline 应返回可追踪的 task_id（不跑真实长任务；to_thread 不执行真管线）。"""

    async def _fake_to_thread(*_a: object, **_k: object) -> str:
        return "ok"

    with patch("src.oligo.tools.miner_tools.asyncio.to_thread", side_effect=_fake_to_thread):
        result = asyncio.run(daily_paper_pipeline(skip_telegram=True))
    assert "[Task Started]" in result
    assert "Daily pipeline" in result
    assert "check_task_status" in result
