"""MW.4：PromptComposer / ChimeraAgent 中间件回归（与 docs/PROMPT_MIDDLEWARE.md 同步）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.crucible.core.schemas import PromptStage
from src.oligo.core.prompt_composer import FINAL_GUARDRAIL_TEXT, get_prompt_composer
from tests.oligo.conftest import MockLLMClient


# 固定 fixture：与 MW.4 文档中回归说明一致；变更模板时请重算 *_BASELINE_BYTES 并更新文档
_REGRESSION_CONTEXT: dict[str, Any] = {
    "system_core": "CORE" * 40,
    "skill_override": "",
    "persona": "PERSONA" * 10,
    "authors_note": "",
    "tool_list": "- (no tools are available in this session)",
    "timestamp": "2000-01-01T00:00:00+00:00",
}

# skill_override 空、有 persona 且与 system_core 不同、无 authors；含两阶段 dynamic_timestamp
_ROUTER_ACTIVE: set[str] = {
    "router_core",
    "router_tool_registry",
    "dynamic_timestamp",
}
# 无 skill、有 persona 覆盖、无 authors
_FINAL_REGRESSION: set[str] = {
    "final_system_core",
    "final_guardrail",
    "dynamic_timestamp",
    "final_persona_override",
}

# MW.4 锁定：_combined_regression_prompt_bytes() 在默认组件下的 UTF-8 长度
MW4_COMBINED_PROMPT_BASELINE_BYTES = 2066


def _combined_regression_prompt_bytes() -> int:
    composer = get_prompt_composer()
    ctx = dict(_REGRESSION_CONTEXT)
    rs, rd = composer.compose(
        PromptStage.ROUTER,
        ctx,
        active_ids=_ROUTER_ACTIVE,
    )
    fs, fd = composer.compose(
        PromptStage.FINAL,
        ctx,
        active_ids=_FINAL_REGRESSION,
    )
    r_full = f"{rs}\n\n{rd}".strip()
    f_full = f"{fs}\n\n{fd}".strip()
    return len((r_full + f_full).encode("utf-8"))


def test_mw4_baseline_byte_lock_unchanged() -> None:
    """若本断言失败，说明模板或 active 集合与 MW.4 锁定不一致；请有意更新 MW4_COMBINED_PROMPT_BASELINE_BYTES 与文档。"""
    assert _combined_regression_prompt_bytes() == MW4_COMBINED_PROMPT_BASELINE_BYTES


def test_router_stable_section_byte_identical_on_repeated_compose() -> None:
    """相同 context 下 Router 的 stable 段两次 compose 字节级一致（供 prefix cache 语义）。"""
    composer = get_prompt_composer()
    ctx = dict(_REGRESSION_CONTEXT)
    a1, d1 = composer.compose(
        PromptStage.ROUTER,
        ctx,
        active_ids=_ROUTER_ACTIVE,
    )
    a2, d2 = composer.compose(
        PromptStage.ROUTER,
        ctx,
        active_ids=_ROUTER_ACTIVE,
    )
    assert a1 == a2
    assert a1.encode("utf-8") == a2.encode("utf-8")
    # 动态段均含 timestamp，仍应一致（同一 context）
    assert d1 == d2


def test_final_system_without_skill_has_no_skill_directive() -> None:
    composer = get_prompt_composer()
    ctx = {
        **_REGRESSION_CONTEXT,
        "skill_override": "",
    }
    ids = {
        "final_system_core",
        "final_guardrail",
        "dynamic_timestamp",
        "final_persona_override",
    }
    stable, dynamic = composer.compose(
        PromptStage.FINAL,
        ctx,
        active_ids=ids,
    )
    full = f"{stable}\n\n{dynamic}".strip()
    assert "[SKILL DIRECTIVE]" not in full


def test_final_system_always_has_execution_context_guardrail() -> None:
    composer = get_prompt_composer()
    ctx = dict(_REGRESSION_CONTEXT)
    ids = {
        "final_system_core",
        "final_guardrail",
        "dynamic_timestamp",
    }
    stable, dynamic = composer.compose(PromptStage.FINAL, ctx, active_ids=ids)
    full = f"{stable}\n\n{dynamic}".strip()
    assert "[EXECUTION CONTEXT]" in full
    assert FINAL_GUARDRAIL_TEXT.split("\n", 1)[0] in full


def test_persona_equals_system_core_suppresses_persona_override() -> None:
    pytest.importorskip("openai")
    from src.oligo.core.agent import ChimeraAgent

    core = "  shared_persona_body  "
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "hi"}],
        system_core=core,
        skill_override=None,
        llm_client=MockLLMClient(),
        persona=core,
        authors_note=None,
    )
    final = agent._final_persona_system_content()
    assert "[PERSONA OVERRIDE]" not in final


def test_overall_prompt_length_within_mw4_migration_budget() -> None:
    """整体 prompt（Router+Final 拼接）UTF-8 长度不超过 MW.4 锁定基线的 110%。"""
    n = _combined_regression_prompt_bytes()
    limit = (MW4_COMBINED_PROMPT_BASELINE_BYTES * 110 + 99) // 100
    assert n <= limit, f"combined={n} exceeds 110% of baseline {MW4_COMBINED_PROMPT_BASELINE_BYTES} (cap {limit})"


def test_full_theater_pass_path_runs() -> None:
    """完整跑通一次剧场（Router <PASS> → Final），不断言 LLM 内容。"""
    pytest.importorskip("openai")

    async def _run() -> None:
        from src.oligo.core.agent import ChimeraAgent

        client = MockLLMClient(probe_response="<PASS>", final_response="done.")
        agent = ChimeraAgent(
            raw_messages=[{"role": "user", "content": "ping"}],
            system_core="You are a test assistant.",
            skill_override=None,
            llm_client=client,
            router_client=client,
        )
        n = 0
        async for _ in agent.run_theater():
            n += 1
        assert n >= 1
        assert client.probe_call_count >= 1
        assert client.final_call_count >= 1

    asyncio.run(_run())
