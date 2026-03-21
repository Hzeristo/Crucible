"""
存根工具：模拟金库搜索，用于 ReAct 引擎测试。
"""

from __future__ import annotations

import asyncio


async def mock_search_vault(query: str) -> str:
    """
    模拟异步金库搜索，带 I/O 延迟。
    """
    await asyncio.sleep(1.5)
    return "Mock Tool Result: The vault says Titans is flawed."
