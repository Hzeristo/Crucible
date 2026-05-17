"""Tests for PromptComposer (MW.1 skeleton)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from xml.etree import ElementTree as ET

from src.crucible.core.schemas import PromptComponent, PromptStage
from src.oligo.core.prompt_composer import (
    PromptComposer,
    _render_xml_structured,
    get_prompt_composer,
)


def _make(
    comp_id: str,
    stage: PromptStage,
    priority: int,
    cacheable: bool,
    template: str,
) -> PromptComponent:
    return PromptComponent(
        id=comp_id,
        stage=stage,
        priority=priority,
        cacheable=cacheable,
        template=template,
    )


def test_register_duplicate_id_raises() -> None:
    c = PromptComposer()
    a = _make("a", PromptStage.ROUTER, 10, True, "x")
    c.register(a)
    b = _make("a", PromptStage.FINAL, 20, True, "y")
    with pytest.raises(ValueError, match="Duplicate component id: a"):
        c.register(b)


def test_compose_order_by_priority_desc() -> None:
    c = PromptComposer()
    c.register(_make("low", PromptStage.ROUTER, 10, True, "L"))
    c.register(_make("high", PromptStage.ROUTER, 100, True, "H"))
    c.register(_make("mid", PromptStage.ROUTER, 50, True, "M"))
    stable, dynamic = c.compose(PromptStage.ROUTER, {})
    assert dynamic == ""
    assert stable == "H\n\nM\n\nL"


def test_compose_separates_cacheable() -> None:
    c = PromptComposer()
    c.register(_make("s", PromptStage.FINAL, 100, True, "stable text"))
    c.register(_make("d", PromptStage.FINAL, 50, False, "dynamic {x}"))
    stable, dynamic = c.compose(PromptStage.FINAL, {"x": "DYN"})
    assert stable == "stable text"
    assert dynamic == "dynamic DYN"


def test_active_ids_filter() -> None:
    c = PromptComposer()
    c.register(_make("keep", PromptStage.ROUTER, 100, True, "K"))
    c.register(_make("drop", PromptStage.ROUTER, 90, True, "X"))
    stable, _ = c.compose(
        PromptStage.ROUTER, {}, active_ids={"keep"}
    )
    assert stable == "K"


def test_both_stage_appears_in_router_and_final() -> None:
    c = PromptComposer()
    c.register(_make("both_only", PromptStage.BOTH, 100, True, "B"))
    s_r, _ = c.compose(PromptStage.ROUTER, {})
    s_f, _ = c.compose(PromptStage.FINAL, {})
    assert s_r == "B"
    assert s_f == "B"


def test_context_placeholder_rendering() -> None:
    c = PromptComposer()
    c.register(
        _make("t1", PromptStage.MESSAGE_INJECTION, 10, True, "Hello, {name} — {n:d}")
    )
    stable, _ = c.compose(
        PromptStage.MESSAGE_INJECTION, {"name": "world", "n": 42}
    )
    assert stable == "Hello, world — 42"


def test_get_prompt_composer_singleton_and_stub() -> None:
    a = get_prompt_composer()
    b = get_prompt_composer()
    assert a is b
    assert a.get_component("nope") is None
    demo = a.get_component("retrieval_context_demo")
    assert demo is not None
    assert demo.renderer == "xml_structured"
    assert isinstance(demo.template, dict)


def test_render_xml_structured_nested_is_parseable_xml() -> None:
    xmls = _render_xml_structured(
        {
            "outer": {"inner": [1, 2, 3], "nested": {"k": "v"}, "flag": True},
            "meta": "x",
        }
    )
    root = ET.fromstring(xmls)
    assert root.tag == "structured"
    outer = root.find("outer")
    assert outer is not None
    inner_el = outer.find("inner")
    assert inner_el is not None
    items = inner_el.findall("item")
    assert [t.text for t in items] == ["1", "2", "3"]
    assert outer.find("nested") is not None
    assert outer.find("nested").find("k").text == "v"
    flag_el = outer.find("flag")
    assert flag_el is not None and flag_el.text == "true"
    assert root.find("meta") is not None and root.find("meta").text == "x"


def test_prompt_component_renderer_validation() -> None:
    with pytest.raises(ValidationError):
        PromptComponent(
            id="bad_xml",
            stage=PromptStage.ROUTER,
            priority=1,
            cacheable=True,
            renderer="xml_structured",
            template="not-dict",
        )
    with pytest.raises(ValidationError):
        PromptComponent(
            id="bad_text",
            stage=PromptStage.ROUTER,
            priority=1,
            cacheable=True,
            renderer="text",
            template={"x": 1},
        )


def test_compose_retrieval_context_demo_when_active() -> None:
    composer = get_prompt_composer()
    stable, dynamic = composer.compose(
        PromptStage.ROUTER,
        {"tool_list": "- demo", "skill_override": "", "persona": "", "authors_note": ""},
        active_ids={"retrieval_context_demo"},
    )
    blob = f"{stable}\n\n{dynamic}".strip()
    assert "<structured>" in blob
    assert "<retrieval>" in blob
    assert "<source>demo</source>" in blob
    assert "<title>Example</title>" in blob
    ET.fromstring(blob)  # well-formed
