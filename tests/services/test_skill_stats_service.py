"""Tests for SkillStatsService."""

from __future__ import annotations

import asyncio
import json

from src.crucible.services.skill_stats_service import SkillStatsService


def test_record_and_get_stats(tmp_path):
    p = tmp_path / "skill_stats.json"
    svc = SkillStatsService(p)

    async def run():
        await svc.record_usage("my_skill", True, 100)
        await svc.record_usage("my_skill", False, 200)
        return await svc.get_stats("my_skill")

    s = asyncio.run(run())
    assert s["usage_count"] == 2
    assert s["success_rate"] == 0.5
    assert s["avg_tokens"] == 150

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["my_skill"]["usage_count"] == 2
    assert data["my_skill"]["success_count"] == 1
    assert data["my_skill"]["total_tokens"] == 300
    assert "last_used" in data["my_skill"]
    assert len(data["my_skill"]["feedback_history"]) == 2


def test_feedback_history_trim(tmp_path):
    p = tmp_path / "skill_stats.json"
    svc = SkillStatsService(p)

    async def run():
        for _ in range(105):
            await svc.record_usage("t", True, 1)

    asyncio.run(run())
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data["t"]["feedback_history"]) == 100


def test_get_stats_empty_skill_id(tmp_path):
    svc = SkillStatsService(tmp_path / "x.json")

    async def run():
        a = await svc.get_stats("")
        b = await svc.get_stats("   ")
        return a, b

    a, b = asyncio.run(run())
    assert a == {"usage_count": 0, "success_rate": 0.0, "avg_tokens": 0}
    assert b == {"usage_count": 0, "success_rate": 0.0, "avg_tokens": 0}
