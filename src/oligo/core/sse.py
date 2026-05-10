"""标准 SSE 行构造（Named Event），供 Oligo agent 与 API 层共用。"""

from __future__ import annotations

import json


def sse_event(event_type: str, data: dict) -> str:
    """生成带 ``event:`` 的 SSE 文本帧，``data`` 为 JSON 对象。"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
 