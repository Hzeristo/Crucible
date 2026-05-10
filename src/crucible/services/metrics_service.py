"""Append-only request metrics persisted under the Chimera data root (e.g. ``metrics.json``)."""

from __future__ import annotations

import json
from pathlib import Path


class MetricsService:
    def __init__(self, metrics_path: Path) -> None:
        self.metrics_path = metrics_path
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

    def record_request(
        self,
        success: bool,
        latency_ms: float,
        tokens: int,
        skill_id: str | None = None,
    ) -> None:
        """Record one completed agent invoke (stream lifecycle)."""
        metrics = self._load_metrics()

        metrics["total_requests"] = metrics.get("total_requests", 0) + 1
        if success:
            metrics["successful_requests"] = metrics.get("successful_requests", 0) + 1

        if "latencies" not in metrics:
            metrics["latencies"] = []
        metrics["latencies"].append(latency_ms)
        metrics["latencies"] = metrics["latencies"][-100:]

        metrics["total_tokens"] = metrics.get("total_tokens", 0) + max(0, tokens)

        if skill_id:
            if "skills" not in metrics:
                metrics["skills"] = {}
            if skill_id not in metrics["skills"]:
                metrics["skills"][skill_id] = {"count": 0, "tokens": 0}
            metrics["skills"][skill_id]["count"] += 1
            metrics["skills"][skill_id]["tokens"] += max(0, tokens)

        self._save_metrics(metrics)

    def get_summary(self) -> dict:
        """Aggregate summary for dashboards (mirrors Tauri ``get_system_metrics`` shape)."""
        metrics = self._load_metrics()

        total = metrics.get("total_requests", 0)
        success = metrics.get("successful_requests", 0)
        latencies = metrics.get("latencies", [])

        return {
            "total_requests": total,
            "success_rate": success / total if total > 0 else 0.0,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "total_tokens": metrics.get("total_tokens", 0),
            "top_skills": self._get_top_skills(metrics),
            "tool_stats": self.get_tool_stats(),
            "wash_stats": self.get_wash_stats(),
        }

    def _get_top_skills(self, metrics: dict) -> list[dict]:
        skills = metrics.get("skills", {})
        sorted_skills = sorted(
            skills.items(),
            key=lambda x: x[1]["count"],
            reverse=True,
        )
        return [
            {"id": skill_id, **stats}
            for skill_id, stats in sorted_skills[:5]
        ]

    def record_tool_call(
        self,
        tool_name: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record one executed tool invocation (allowed path, after deadline wrapper)."""
        name = (tool_name or "").strip()
        if not name:
            return

        metrics = self._load_metrics()
        if "tools" not in metrics:
            metrics["tools"] = {}

        if name not in metrics["tools"]:
            metrics["tools"][name] = {
                "count": 0,
                "success": 0,
                "latencies": [],
            }

        tool_stats = metrics["tools"][name]
        tool_stats["count"] = int(tool_stats.get("count", 0)) + 1
        if success:
            tool_stats["success"] = int(tool_stats.get("success", 0)) + 1
        if "latencies" not in tool_stats:
            tool_stats["latencies"] = []
        tool_stats["latencies"].append(float(latency_ms))
        tool_stats["latencies"] = tool_stats["latencies"][-50:]

        self._save_metrics(metrics)

    def get_tool_stats(self) -> list[dict]:
        """Per-tool frequency, success rate, and rolling average latency."""
        metrics = self._load_metrics()
        tools = metrics.get("tools", {})
        result: list[dict] = []
        for tool_name, stats in tools.items():
            if not isinstance(stats, dict):
                continue
            count = int(stats.get("count", 0))
            succ = int(stats.get("success", 0))
            latencies = stats.get("latencies", [])
            if not isinstance(latencies, list):
                latencies = []
            nums = [float(x) for x in latencies if isinstance(x, (int, float))]
            result.append(
                {
                    "name": tool_name,
                    "count": count,
                    "success_rate": succ / count if count > 0 else 0.0,
                    "avg_latency_ms": sum(nums) / len(nums) if nums else 0.0,
                }
            )
        return sorted(result, key=lambda x: x["count"], reverse=True)

    def record_wash(
        self,
        original_length: int,
        washed_length: int,
        tool_name: str,
    ) -> None:
        """Record one Cognitive Filter (LLM wash or degraded truncation) outcome."""
        name = (tool_name or "").strip()
        if not name:
            name = "_unknown"

        o_len = max(0, int(original_length))
        w_len = max(0, int(washed_length))

        metrics = self._load_metrics()
        if "wash" not in metrics:
            metrics["wash"] = {
                "total_washes": 0,
                "total_original_chars": 0,
                "total_washed_chars": 0,
                "by_tool": {},
            }

        wash_stats = metrics["wash"]
        if not isinstance(wash_stats, dict):
            wash_stats = {
                "total_washes": 0,
                "total_original_chars": 0,
                "total_washed_chars": 0,
                "by_tool": {},
            }
            metrics["wash"] = wash_stats

        wash_stats["total_washes"] = int(wash_stats.get("total_washes", 0)) + 1
        wash_stats["total_original_chars"] = int(
            wash_stats.get("total_original_chars", 0)
        ) + o_len
        wash_stats["total_washed_chars"] = int(
            wash_stats.get("total_washed_chars", 0)
        ) + w_len

        if "by_tool" not in wash_stats or not isinstance(wash_stats["by_tool"], dict):
            wash_stats["by_tool"] = {}

        if name not in wash_stats["by_tool"]:
            wash_stats["by_tool"][name] = {
                "count": 0,
                "original": 0,
                "washed": 0,
            }

        tool_wash = wash_stats["by_tool"][name]
        tool_wash["count"] = int(tool_wash.get("count", 0)) + 1
        tool_wash["original"] = int(tool_wash.get("original", 0)) + o_len
        tool_wash["washed"] = int(tool_wash.get("washed", 0)) + w_len

        self._save_metrics(metrics)

    def get_wash_stats(self) -> dict:
        """Aggregate wash compression; ``by_tool`` mirrors persisted shape."""
        metrics = self._load_metrics()
        wash = metrics.get("wash", {})
        if not isinstance(wash, dict):
            wash = {}

        total_original = int(wash.get("total_original_chars", 0))
        total_washed = int(wash.get("total_washed_chars", 0))

        by_tool = wash.get("by_tool", {})
        if not isinstance(by_tool, dict):
            by_tool = {}

        return {
            "total_washes": int(wash.get("total_washes", 0)),
            "avg_compression_rate": (
                (1.0 - total_washed / total_original) if total_original > 0 else 0.0
            ),
            "by_tool": by_tool,
        }

    def _load_metrics(self) -> dict:
        if not self.metrics_path.exists():
            return {}
        try:
            return json.loads(self.metrics_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_metrics(self, metrics: dict) -> None:
        self.metrics_path.write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
