"""统一 system / message prompt 组装器；默认片段见 ``_register_default_components``。"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from src.crucible.core.schemas import PromptComponent, PromptStage
from src.oligo.tools.registry import get_tool_registry

_global_composer: PromptComposer | None = None

# 与历史 ``agent._FINAL_GUARDRAIL`` 一致；在 composer 中作为模板，由 ``"\\n\\n".join`` 与上段衔接（模板本身无前置换行）。
FINAL_GUARDRAIL_TEXT = (
    "[EXECUTION CONTEXT]\n"
    "All tool execution is complete. The tool results are already provided above. "
    "You MUST NOT output any <CMD:...> tags or pseudo-tool-call syntax. "
    "Synthesize the available evidence and respond in natural language only. "
    "If evidence is insufficient, say so honestly — do NOT fabricate tool calls. "
    "If a natural-language assistant turn appears after tool results, treat it as a router draft to "
    "expand and rephrase in your role."
)

# 字面大括号在 format 中需加倍；逻辑与未拆分前单块字符串相同。
# 末尾不换行，由与 ``router_tool_registry`` 的 ``"\\n\\n".join`` 形成与旧版 ``accurately.\\n\\nAvailable`` 相同间距。
ROUTER_INTRO = (
    "You are the Chimera OS local router. Analyze the user's input and decide whether a tool "
    "is needed to answer accurately.\n\n"
    "Tool calling syntax (you may use either format):\n\n"
    "Format A (XML, preferred):\n"
    "<tool_call name=\"search_vault\">\n"
    "  <args>{{\"query\": \"...\"}}</args>\n"
    "</tool_call>\n\n"
    "Format B (CMD, legacy compatible):\n"
    "<CMD:search_vault({{\"query\": \"...\"}})>\n\n"
    "You may output multiple tool_call/CMD blocks in one response (they will run in parallel)."
)

ROUTER_POST_TOOLS = (
    "Rules:\n"
    "1. If a tool is needed, use either Format A (XML) or Format B (<CMD:...>) from the syntax "
    "above: a single JSON object inside <args>...</args> or inside the CMD parentheses "
    '(e.g. {{"query": "..."}}). You may output more than one tool_call or <CMD> if required. '
    "Use only tools listed below; do not invent tool names or pseudo-calls.\n"
    "2. If no tool is needed, you may either output <PASS> (to let downstream persona/synthesis "
    "run) OR provide a short draft answer in natural language. "
    "If you are not calling a real tool, do not output <tool_call> or <CMD:...>.\n\n"
    "META: When discussing tool-calling syntax in natural language (e.g. explaining XML or "
    "<CMD:...> to a user), always wrap the example in a markdown fenced code block (```) or inline "
    "backticks (`) so the runtime does not treat it as a real tool invocation."
)


def _render_tool_list(allowed: Collection[str] | None = None) -> str:
    """从 ToolRegistry 生成 router 可见的工具列表（与 ``router_tool_registry`` 的 ``{tool_list}`` 对应）。"""
    specs = get_tool_registry().list_specs(allowed=allowed)
    lines: list[str] = []
    for spec in specs:
        line = f"- {spec.name}: {spec.description}"
        if spec.long_running:
            line += " [returns task_id]"
        lines.append(line)
    if not lines:
        return "- (no tools are available in this session)"
    return "\n".join(lines)


class PromptComposer:
    """统一的 system prompt 组装器"""

    def __init__(self) -> None:
        self._components: dict[str, PromptComponent] = {}

    def register(self, component: PromptComponent) -> None:
        """注册一个 prompt 片段"""
        if component.id in self._components:
            raise ValueError(f"Duplicate component id: {component.id}")
        self._components[component.id] = component

    def compose(
        self,
        stage: PromptStage,
        context: dict[str, Any],
        active_ids: set[str] | None = None,
    ) -> tuple[str, str]:
        """
        组装指定 stage 的 system prompt.

        Returns:
            (stable_section, dynamic_section)
        """
        candidates: list[PromptComponent] = []
        for c in self._components.values():
            if _component_matches_stage(c.stage, stage):
                candidates.append(c)

        if active_ids is not None:
            candidates = [c for c in candidates if c.id in active_ids]

        candidates.sort(key=lambda x: x.priority, reverse=True)

        stable_parts: list[str] = []
        dynamic_parts: list[str] = []
        for c in candidates:
            rendered = c.template.format(**context)
            if c.cacheable:
                stable_parts.append(rendered)
            else:
                dynamic_parts.append(rendered)

        stable_section = "\n\n".join(stable_parts)
        dynamic_section = "\n\n".join(dynamic_parts)
        return (stable_section, dynamic_section)

    def get_component(self, component_id: str) -> PromptComponent | None:
        return self._components.get(component_id)


def _component_matches_stage(component_stage: PromptStage, target: PromptStage) -> bool:
    if component_stage == PromptStage.BOTH:
        return target in (PromptStage.ROUTER, PromptStage.FINAL)
    if target == PromptStage.BOTH:
        return component_stage == PromptStage.BOTH
    return component_stage == target


def get_prompt_composer() -> PromptComposer:
    global _global_composer
    if _global_composer is None:
        _global_composer = PromptComposer()
        _register_default_components(_global_composer)
    return _global_composer


def _register_default_components(composer: PromptComposer) -> None:
    """注册 ChimeraAgent 与 MW.0 审计对齐的默认片段。"""

    # --- Router: intro (no tool lines) + tool block + rules/meta ---
    composer.register(
        PromptComponent(
            id="router_core",
            stage=PromptStage.ROUTER,
            priority=100,
            cacheable=True,
            template=ROUTER_INTRO,
        )
    )
    composer.register(
        PromptComponent(
            id="router_tool_registry",
            stage=PromptStage.ROUTER,
            priority=90,
            cacheable=True,
            template="Available tools:\n{tool_list}\n\n" + ROUTER_POST_TOOLS,
        )
    )
    composer.register(
        PromptComponent(
            id="router_skill_directive",
            stage=PromptStage.ROUTER,
            priority=80,
            cacheable=True,
            template=(
                "[USER SKILL DIRECTIVE (FOLLOW THIS FOR YOUR REASONING)]:\n"
                "{skill_override}"
            ),
        )
    )
    # --- Final ---
    composer.register(
        PromptComponent(
            id="final_system_core",
            stage=PromptStage.FINAL,
            priority=100,
            cacheable=True,
            template="{system_core}",
        )
    )
    composer.register(
        PromptComponent(
            id="final_skill_directive",
            stage=PromptStage.FINAL,
            priority=80,
            cacheable=True,
            template="[SKILL DIRECTIVE]\n{skill_override}",
        )
    )
    composer.register(
        PromptComponent(
            id="final_persona_override",
            stage=PromptStage.FINAL,
            priority=60,
            cacheable=True,
            template="\n[PERSONA OVERRIDE]\n{persona}",
        )
    )
    composer.register(
        PromptComponent(
            id="final_authors_note",
            stage=PromptStage.FINAL,
            priority=40,
            cacheable=True,
            template="\n[AUTHOR'S NOTE]\n{authors_note}",
        )
    )
    composer.register(
        PromptComponent(
            id="final_guardrail",
            stage=PromptStage.FINAL,
            priority=10,
            cacheable=True,
            template=FINAL_GUARDRAIL_TEXT,
        )
    )
    composer.register(
        PromptComponent(
            id="dynamic_timestamp",
            stage=PromptStage.BOTH,
            priority=5,
            cacheable=False,
            template="{timestamp}",
        )
    )
