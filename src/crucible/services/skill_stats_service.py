"""Skill 使用统计：与 `~/.chimera/skills/*.json` 定义分离，持久化到 `skill_stats.json`。"""

# TODO: Phase III - 当引入向量化模型后，可以计算：
# - router_accuracy: 选择的工具是否与最终使用的工具一致
# - tool_relevance: 工具结果与用户问题的语义相似度
# - wash_quality: Wash 后的文本与原文的信息保留率

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SkillStatsService:
    """读写 `skill_stats.json`；`record_usage` / `get_stats` 均为异步，共享 `asyncio.Lock`。"""

    def __init__(self, stats_path: Path) -> None:
        self.stats_path = stats_path
        self._lock = asyncio.Lock()

    def _load_stats(self) -> dict[str, Any]:
        if not self.stats_path.is_file():
            return {}
        try:
            raw = self.stats_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save_stats(self, stats: dict[str, Any]) -> None:
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.stats_path.with_suffix(self.stats_path.suffix + ".tmp")
        try:
            text = json.dumps(stats, indent=2, ensure_ascii=False)
            tmp.write_text(text + "\n", encoding="utf-8")
            tmp.replace(self.stats_path)
        except OSError:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
            raise

    def _record_usage_impl(self, sid: str, success: bool, tokens: int) -> None:
        stats = self._load_stats()
        if sid not in stats:
            stats[sid] = {
                "usage_count": 0,
                "success_count": 0,
                "total_tokens": 0,
                "feedback_history": [],
            }

        entry: dict[str, Any] = stats[sid]
        entry["usage_count"] = int(entry.get("usage_count", 0)) + 1
        if success:
            entry["success_count"] = int(entry.get("success_count", 0)) + 1
        entry["total_tokens"] = int(entry.get("total_tokens", 0)) + tokens
        entry["last_used"] = _utc_iso_z()

        hist = entry.get("feedback_history")
        if not isinstance(hist, list):
            hist = []
        hist.append(
            {
                "timestamp": _utc_iso_z(),
                "success": success,
                "tokens": tokens,
            }
        )
        entry["feedback_history"] = hist[-100:]

        self._save_stats(stats)

    def _get_stats_impl(self, sid: str) -> dict[str, Any]:
        stats = self._load_stats()
        if sid not in stats:
            return {"usage_count": 0, "success_rate": 0.0, "avg_tokens": 0}

        s = stats[sid]
        usage = int(s.get("usage_count", 0))
        success_c = int(s.get("success_count", 0))
        total_tok = int(s.get("total_tokens", 0))

        return {
            "usage_count": usage,
            "success_rate": (success_c / usage) if usage > 0 else 0.0,
            "avg_tokens": (total_tok // usage) if usage > 0 else 0,
        }

    async def record_usage(self, skill_id: str, success: bool, tokens: int) -> None:
        """记录一次 Skill 调用（磁盘 IO 在线程池中执行，避免阻塞事件循环）。"""
        sid = skill_id.strip()
        if not sid:
            return
        tok = max(0, int(tokens))
        async with self._lock:
            await asyncio.to_thread(self._record_usage_impl, sid, success, tok)

    async def get_stats(self, skill_id: str) -> dict[str, Any]:
        """返回聚合统计（供 UI / 日志）；不含原始 feedback_history。"""
        sid = skill_id.strip()
        if not sid:
            return {"usage_count": 0, "success_rate": 0.0, "avg_tokens": 0}
        async with self._lock:
            return await asyncio.to_thread(self._get_stats_impl, sid)
