"""
剧场版 ReAct 引擎：静默推理层 + 终极推流层。

废除工具思考阶段的流式截断，采用「前期阻塞探包 + 后期全量推流」架构。
内部消息流严格使用 list[ChatMessage]，在边界处通过 model_dump 与网络层对接。
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, AsyncGenerator

from src.crucible.core.schemas import (
    ChatMessage,
    ExecutedToolResult,
    OligoAgentConfig,
    PlannedToolCall,
    PromptStage,
    ToolCallStatus,
)
from src.crucible.ports.llm.base import LLMClient
from src.crucible.services.metrics_service import MetricsService
from src.oligo.core.prompt_composer import _render_tool_list, get_prompt_composer
from src.oligo.core.sse import sse_event
from src.oligo.core.text_sanitizer import TextSanitizer
from src.oligo.core.tool_protocol import (
    extract_tool_syntax_for_history,
    parse_args_with_repair,
    parse_tool_calls_unified,
    planned_call_id_from_parsed,
)
from src.oligo.tools import TOOL_REGISTRY
from src.oligo.tools.registry import get_tool_registry, partition_tool_calls

logger = logging.getLogger(__name__)

CLIENT_SEVERED_WARNING = (
    "[Oligo] Client forcibly severed the connection. "
    "Neural loop aborted mid-flight. Purging memory."
)


def _client_gone_exception_types() -> tuple[type[BaseException], ...]:
    """Exceptions that mean the HTTP/SSE client dropped — swallow quietly (no traceback spam)."""
    types_list: list[type[BaseException]] = [
        asyncio.CancelledError,
        ConnectionError,
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
    ]
    try:
        from starlette.requests import ClientDisconnect  # type: ignore[attr-defined]

        types_list.append(ClientDisconnect)
    except ImportError:
        pass
    return tuple(types_list)


CLIENT_GONE_EXCEPTIONS: tuple[type[BaseException], ...] = _client_gone_exception_types()


def _looks_like_pipe_broken(exc: BaseException) -> bool:
    """Uvicorn/ASGI sometimes surfaces broken pipes as OSError or RuntimeError."""
    if isinstance(exc, OSError):
        if exc.errno in (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED):
            return True
    msg = str(exc).lower()
    if "disconnect" in msg or "connection reset" in msg or "broken pipe" in msg:
        return True
    if "client" in msg and "disconnect" in msg:
        return True
    return False


def _handle_client_gone() -> None:
    logger.warning(CLIENT_SEVERED_WARNING)


# 工具调用字面量：``tool_protocol``（XML + CMD）；剥离代码块见 ``TextSanitizer``（S0.4）


def _strip_markdown_code_for_cmd_extraction(text: str) -> str:
    """
    从 router probe 中去掉 Markdown 代码块/行内代码，再用于工具调用匹配。

    避免模型在 `` ``` `` 或 `` `...` `` 里**举例** ``<CMD:...>`` 时被误执行。

    委托 :meth:`TextSanitizer.strip_code_blocks_for_tool_matching`。
    """
    return TextSanitizer.strip_code_blocks_for_tool_matching(text)


def _strip_router_dsl_for_backfill(text: str) -> str:
    """
    在 draft backfill 写入 ``self.messages`` 前去掉可见区路由 DSL 字面量（代码块内保留举例）。

    委托 :meth:`TextSanitizer.strip_tool_syntax_in_visible`。
    """
    return TextSanitizer.strip_tool_syntax_in_visible(text)


_THEATER_LLM_OUTER_TIMEOUT_S = 120.0
# Intent-Driven Wash: fallback when LLM call fails (chars + suffix length budget)
_WASH_FALLBACK_CHARS = 1500
_WASH_TRUNC_SUFFIX = "...[TRUNCATED]"
# Router-visible dialogue tail: last two user/assistant rounds (max 4 messages)
_WASH_CONTEXT_MAX_MESSAGES = 4
_WASH_CONTEXT_PER_MSG_CAP = 8000

# Router 探针：极短/仅 ``<PASS>`` 则不回填 assistant（避免把噪声当作草稿塞给 Final）
_ROUTER_TRIVIAL_MAX_CHARS = 30


def _is_router_pass_or_trivial(probe_response: str) -> bool:
    """为 True 时不把 probe 写入 messages（走纯 Final 合成）。"""
    s = (probe_response or "").strip()
    if not s:
        return True
    if s.upper() == "<PASS>":
        return True
    if len(s) < _ROUTER_TRIVIAL_MAX_CHARS:
        return True
    return False

# 工具环专用：动态路由 System 由 ``ChimeraAgent._build_router_system_prompt`` 生成，
# 与 ``TOOL_REGISTRY`` 及 ``allowed_tools`` 对齐，与晚期绑定的 BB / skill 人设分离。

def _sys_telemetry_obj(obj: dict[str, Any]) -> str:
    """Astrocyte 分段反馈：`__SYS_TOOL_CALL__` + JSON（stage / content / tool_name / decision）。"""
    return "__SYS_TOOL_CALL__" + json.dumps(obj, ensure_ascii=False)


def _tool_batch_start_payload(batch: list[PlannedToolCall]) -> dict[str, Any]:
    """TP.4：工具批调度遥测（并发批 / 串行批）。"""
    return {
        "stage": "tool",
        "phase": "batch_start",
        "batch_size": len(batch),
        "concurrency": "parallel" if len(batch) > 1 else "serial",
        "tools": [c.tool_name for c in batch],
    }


def _sse_data(payload: str) -> str:
    """
    将负载打包为单条 SSE data 帧；正文经 JSON 转义，换行与特殊字符不会破坏帧边界。

    Args:
        payload: 原始文本负载，可为空。

    Returns:
        ``data: {"content": "..."}\\n\\n`` 形式；空负载时为 ``data: \\n\\n``。
    """
    if not payload:
        return "data: \n\n"
    safe_json = json.dumps({"content": payload})
    return f"data: {safe_json}\n\n"


# 与 ``_sse_data`` 区分：本函数发出 **命名事件** ``bb-stream-chunk``，供下游（如 Rust）与 ``bb-stream-done`` 分轨解析。
def _sse_chunk(payload: str) -> str:
    return sse_event("bb-stream-chunk", {"content": payload})


def _messages_to_api(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """
    将 Pydantic 消息列表安全转化为网络层（OpenAI 等）接受的字典列表。

    仅保留非 None 字段，满足 API 契约。

    Args:
        messages: 强类型 ChatMessage 列表。

    Returns:
        适用于 chat.completions.create 的 messages 参数格式。
    """
    return [m.model_dump(exclude_none=True) for m in messages]


def _ensure_chat_messages(messages: list[dict[str, Any]] | list[ChatMessage]) -> list[ChatMessage]:
    """
    将外部输入的 list[dict] 或 list[ChatMessage] 强转为 list[ChatMessage]。

    用于 __init__ 的防御式校验，确保内部状态绝对类型安全。

    Args:
        messages: 来自 API 或上层的消息列表，可能是裸 dict。

    Returns:
        经 model_validate 校验后的 list[ChatMessage]。
    """
    if not messages:
        return []
    first = messages[0]
    if isinstance(first, ChatMessage):
        return list(messages)
    return [ChatMessage.model_validate(m) for m in messages]


class ChimeraAgent:
    """
    剧场版 ReAct Agent：路由环（短 System）+ 晚期人设绑定 + 全量推流。

    工作原理简述：
    1. 工具搜寻环：使用动态路由 System（与 ``TOOL_REGISTRY`` / ``allowed_tools`` 一致；
       可选追加 ``[Skill Directives]``）+ 纯净
       ``raw_messages``；非流式 ``generate_raw_text`` 探包，避免 ``<CMD>`` 截断。
    2. 晚期绑定：一旦本轮回答不含 ``<CMD>``，丢弃路由 System，以 L1（``system_core`` +
       可选 ``[SKILL DIRECTIVE]``）及 L2/L3 分层拼成最终 System 置于历史顶端（含已注入的
       ``[SYSTEM TOOL RESULTS]``），再发起**最后一次** ``generate_raw_text``，再推流。
    3. 边界处通过 ``model_dump`` 与网络层对接；异常与 SSE 帧格式保持不变。
    """

    def __init__(
        self,
        raw_messages: list[dict[str, Any]] | list[ChatMessage],
        system_core: str,
        skill_override: str | None,
        llm_client: LLMClient,
        wash_client: LLMClient | None = None,
        router_client: LLMClient | None = None,
        max_turns: int = 5,
        allowed_tools: list[str] | None = None,
        agent_config: OligoAgentConfig | None = None,
        persona: str | None = None,
        authors_note: str | None = None,
        metrics_service: MetricsService | None = None,
    ) -> None:
        """
        Args:
            raw_messages: 纯净 user/assistant 对话（无网关预拼 System）。
            system_core: L1 人设基座；仅在最终入模前与 L2/L3 分层拼接（不含 Author's Note 预混）。
            skill_override: 技能覆写文案；参与路由环 ``[Skill Directives]`` 与最终 System 基线拼接。
            llm_client: 需满足 ``LLMClient`` 协议所要求的方法集。
            wash_client: 可选廉价 OpenAI 兼容客户端，用于工具结果 Wash 压缩；缺省时回落到 ``llm_client``。
            router_client: 可选路由探针专用客户端（lifespan 内单例）；缺省时路由探针回落到 ``llm_client``（每请求 Working）。
            max_turns: 最大 ReAct 轮次。
            allowed_tools: 可执行工具白名单；为 None 时不做限制。
            agent_config: 工具超时与 wash 策略；缺省使用内置 ``OligoAgentConfig()``。
            persona: L2 可选，Final Stream 以 ``[PERSONA OVERRIDE]`` 注入；与 ``system_core`` 去空白后相同时跳过 L2。
            authors_note: L3 可选，以 ``[AUTHOR'S NOTE]`` 置于最后注入。
            metrics_service: 可选；若提供则记录工具调用延迟与成败（Oligo HTTP 层注入）。
        """
        self._system_core = system_core
        self._persona = persona
        self._authors_note = authors_note
        self._skill_override = (skill_override or "").strip() or None
        self.allowed_tools = allowed_tools
        self._agent_config = agent_config or OligoAgentConfig()

        self.raw_messages: list[ChatMessage] = _ensure_chat_messages(raw_messages)
        self.llm_client = llm_client
        self.wash_client = wash_client
        self._router_client: LLMClient = router_client or llm_client
        self.max_turns = max_turns
        self._metrics_service = metrics_service

        router_body = self._build_router_system_prompt()

        self.messages: list[ChatMessage] = [
            ChatMessage(role="system", content=router_body),
            *[m.model_copy(deep=True) for m in self.raw_messages],
        ]

    def _build_tool_list_text(self) -> str:
        """按 ToolRegistry 元数据与 ``allowed_tools`` 生成 router 工具列表行。"""
        return _render_tool_list(self.allowed_tools)

    def _compute_active_router_components(self) -> set[str]:
        """决定 Router system 中启用的 ``PromptComponent`` id（含 ``dynamic_timestamp``）。

        ``skill`` 类条件与 MW.0 一致；persona / authors 仅由
        :meth:`_compute_active_final_components` 控制，不属于 Router system。
        """
        ids = {
            "router_core",
            "router_tool_registry",
            "dynamic_timestamp",
        }
        if self._skill_override:
            ids.add("router_skill_directive")
        return ids

    def _compute_active_final_components(self) -> set[str]:
        """决定 Final system 中启用的 fragment id（含 ``dynamic_timestamp``）。"""
        ids = {
            "final_system_core",
            "final_guardrail",
            "dynamic_timestamp",
        }
        if self._skill_override:
            ids.add("final_skill_directive")
        if self._persona and self._persona.strip() != self._system_core.strip():
            ids.add("final_persona_override")
        if self._authors_note:
            ids.add("final_authors_note")
        return ids

    def _prompt_context(self) -> dict[str, Any]:
        """供 ``PromptComposer.compose`` 使用的统一上下文（未使用的键由非活动组件忽略）。"""
        return {
            "system_core": self._system_core,
            "skill_override": self._skill_override or "",
            "persona": self._persona or "",
            "authors_note": self._authors_note or "",
            "tool_list": self._build_tool_list_text(),
            "timestamp": datetime.now().isoformat(),
        }

    def _build_router_system_prompt(self) -> str:
        """从 ``PromptComposer`` 组装路由 System 文案（与 MW.0 拆分前语义一致）。"""
        composer = get_prompt_composer()
        context = self._prompt_context()
        active_ids = self._compute_active_router_components()
        stable, dynamic = composer.compose(
            stage=PromptStage.ROUTER,
            context=context,
            active_ids=active_ids,
        )
        logger.debug(
            "[Prompt] router compose stable_len=%s dynamic_len=%s",
            len(stable),
            len(dynamic),
        )
        return f"{stable}\n\n{dynamic}".strip()

    def _apply_history_sanitizer_to_messages(self) -> None:
        """位点 C：在送往 LLM 前清洗 ``self.messages`` 历史（层 3）。"""
        self.messages = TextSanitizer.sanitize_messages_history(self.messages)  # type: ignore[assignment]

    def _final_persona_system_content(self) -> str:
        """
        构建 Final Stream 的 System Prompt：L1（core + 技能）> L2（persona）> L3（作者注）>
        guardrail + 动态时间戳；禁止 Final 输出 ``<CMD:...>`` 的约束在 ``final_guardrail`` 片段中。
        """
        composer = get_prompt_composer()
        context = self._prompt_context()
        active_ids = self._compute_active_final_components()
        stable, dynamic = composer.compose(
            stage=PromptStage.FINAL,
            context=context,
            active_ids=active_ids,
        )
        logger.debug(
            "[Prompt] final compose stable_len=%s dynamic_len=%s",
            len(stable),
            len(dynamic),
        )
        return f"{stable}\n\n{dynamic}".strip()

    def _parse_tool_args(self, raw_args: str) -> dict[str, Any]:
        """Parse tool args（含 TP.3 保守修复）；返回值不含 repairs 列表。"""
        args, _ = parse_args_with_repair(raw_args)
        return args

    def _parse_tool_calls(self, probe_response: str) -> list[PlannedToolCall]:
        """
        Parse ``<tool_call>``（XML）与 ``<CMD:...>`` 为结构化计划并完成 allowlist 解析。

        匹配前先去掉 Markdown 代码块/行内代码，避免「举例」中的工具标签被执行（S0.4 Fix A）。

        参数须 parse 为合法 JSON object；失败则 ``allowed=False``（S0.4 Fix B）。
        """
        planned: list[PlannedToolCall] = []
        for_matching = _strip_markdown_code_for_cmd_extraction(probe_response)
        for pc in parse_tool_calls_unified(for_matching):
            tool_name = pc.tool_name
            raw_args = pc.raw_args
            plan_id = planned_call_id_from_parsed(pc)
            if not re.fullmatch(r"[a-zA-Z0-9_]+", tool_name):
                planned.append(
                    PlannedToolCall(
                        id=plan_id,
                        tool_name=tool_name,
                        raw_args=raw_args,
                        args={},
                        allowed=False,
                        deny_reason=(
                            f"Malformed tool name {tool_name!r}; "
                            "expected one token [a-zA-Z0-9_]+"
                        ),
                        repairs_applied=[],
                    )
                )
                continue
            try:
                args, repairs_applied = parse_args_with_repair(raw_args)
            except ValueError as e:
                logger.warning(
                    "[Oligo] Parser: tool args parse failed for tool=%s (%s): %s; "
                    "denying call (invalid args JSON).",
                    tool_name,
                    pc.source_format,
                    e,
                )
                planned.append(
                    PlannedToolCall(
                        id=plan_id,
                        tool_name=tool_name,
                        raw_args=raw_args,
                        args={},
                        allowed=False,
                        deny_reason=(
                            f"Invalid JSON in tool args for tool '{tool_name}': {e}"
                        ),
                        repairs_applied=[],
                    )
                )
                continue
            if repairs_applied:
                logger.info(
                    "[Tool] Args repaired for %s: %s",
                    tool_name,
                    repairs_applied,
                )
            if tool_name not in TOOL_REGISTRY:
                allowed = False
                deny_reason = (
                    f"Tool '{tool_name}' is not a registered tool. "
                    f"Available: {list(TOOL_REGISTRY.keys())}"
                )
            elif self.allowed_tools is None:
                allowed = True
                deny_reason = None
            elif tool_name in self.allowed_tools:
                allowed = True
                deny_reason = None
            else:
                allowed = False
                deny_reason = (
                    f"Tool '{tool_name}' is not allowed under current skill."
                )
            planned.append(
                PlannedToolCall(
                    id=plan_id,
                    tool_name=tool_name,
                    raw_args=raw_args,
                    args=args,
                    allowed=allowed,
                    deny_reason=deny_reason,
                    repairs_applied=repairs_applied,
                )
            )
        return planned

    def _maybe_record_tool_call(
        self, tool_name: str, success: bool, latency_ms: float
    ) -> None:
        svc = self._metrics_service
        if svc is not None:
            svc.record_tool_call(tool_name, success, latency_ms)

    def _maybe_record_wash(
        self, original_length: int, washed_length: int, tool_name: str
    ) -> None:
        svc = self._metrics_service
        if svc is not None:
            svc.record_wash(original_length, washed_length, tool_name)

    async def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        """
        从 ``TOOL_REGISTRY`` 调度工具执行；入参为 planning 阶段已解析的 dict。

        Returns:
            工具执行结果字符串；出错时返回 "Error: ..." 描述。
        """
        if tool_name not in TOOL_REGISTRY:
            out = f"Error: Tool '{tool_name}' is not recognized by the Chimera OS."
            logger.info(
                "[Tool] X-RAY Tool '%s' returned (first 300 chars): %r",
                tool_name,
                out[:300],
            )
            return out
        fn = TOOL_REGISTRY[tool_name]
        try:
            result = await fn(**args)
            out = str(result)
        except TypeError as e:
            out = f"Error: Tool '{tool_name}' invalid args: {e}"
        logger.info(
            "[Tool] X-RAY Tool '%s' returned (first 300 chars): %r",
            tool_name,
            out[:300],
        )
        return out

    async def _execute_tool_with_deadline(
        self, tool_name: str, args: dict[str, Any]
    ) -> str:
        """调度层死线：不修改 `_execute_tool`，仅包裹 `wait_for`。"""
        deadline = self._agent_config.tool_execution_deadline_seconds
        try:
            return await asyncio.wait_for(
                self._execute_tool(tool_name, args),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[Tool] Tool deadline exceeded (%.0fs): tool=%s",
                deadline,
                tool_name,
            )
            return (
                f"[TOOL TIMEOUT]: Execution exceeded {deadline}s and was terminated."
            )

    @staticmethod
    def _split_planned_denied_allowed(
        planned_calls: list[PlannedToolCall],
    ) -> tuple[list[ExecutedToolResult], list[PlannedToolCall]]:
        """拒绝项物化为 ``ExecutedToolResult``；允许项保留为计划列表（顺序不变）。"""
        denied_out: list[ExecutedToolResult] = []
        allowed_plans: list[PlannedToolCall] = []
        for plan in planned_calls:
            if not plan.allowed:
                dr = plan.deny_reason
                if dr is None:
                    dr = "Tool invocation denied."
                denied_out.append(
                    ExecutedToolResult(
                        call_id=plan.id,
                        tool_name=plan.tool_name,
                        args=plan.args,
                        status=ToolCallStatus.DENIED,
                        raw_result=f"[Permission Denied] {dr}",
                        washed_result=None,
                        error_message=dr,
                        elapsed_ms=None,
                    )
                )
            else:
                allowed_plans.append(plan)
        return denied_out, allowed_plans

    async def _execute_tool_plan_batch(
        self,
        batch: list[PlannedToolCall],
    ) -> list[ExecutedToolResult]:
        """执行单批计划：单工具直接 await；多工具 ``asyncio.gather``（与历史异常语义一致）。"""
        async def _run_one(plan: PlannedToolCall) -> ExecutedToolResult:
            t0 = time.perf_counter()
            try:
                raw = await self._execute_tool_with_deadline(
                    plan.tool_name, plan.args
                )
            except asyncio.CancelledError:
                raise
            except CLIENT_GONE_EXCEPTIONS:
                raise
            except Exception as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._maybe_record_tool_call(plan.tool_name, False, elapsed_ms)
                return ExecutedToolResult(
                    call_id=plan.id,
                    tool_name=plan.tool_name,
                    args=plan.args,
                    status=ToolCallStatus.ERROR,
                    raw_result=f"Error: {e}",
                    washed_result=None,
                    error_message=str(e),
                    elapsed_ms=int(elapsed_ms),
                )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if raw.startswith("[TOOL TIMEOUT]:"):
                self._maybe_record_tool_call(plan.tool_name, False, elapsed_ms)
                return ExecutedToolResult(
                    call_id=plan.id,
                    tool_name=plan.tool_name,
                    args=plan.args,
                    status=ToolCallStatus.TIMEOUT,
                    raw_result=raw,
                    washed_result=None,
                    error_message=raw,
                    elapsed_ms=int(elapsed_ms),
                )
            ok = not str(raw).startswith("Error:")
            self._maybe_record_tool_call(plan.tool_name, ok, elapsed_ms)
            return ExecutedToolResult(
                call_id=plan.id,
                tool_name=plan.tool_name,
                args=plan.args,
                status=ToolCallStatus.SUCCESS,
                raw_result=str(raw),
                washed_result=None,
                error_message=None,
                elapsed_ms=int(elapsed_ms),
            )

        if len(batch) == 1:
            return [await _run_one(batch[0])]

        results = await asyncio.gather(
            *(_run_one(p) for p in batch),
            return_exceptions=True,
        )
        executed: list[ExecutedToolResult] = []
        for plan, r in zip(batch, results):
            if isinstance(r, BaseException):
                if isinstance(r, CLIENT_GONE_EXCEPTIONS):
                    raise r
                if not isinstance(r, Exception):
                    raise r
                self._maybe_record_tool_call(plan.tool_name, False, 0.0)
                executed.append(
                    ExecutedToolResult(
                        call_id=plan.id,
                        tool_name=plan.tool_name,
                        args=plan.args,
                        status=ToolCallStatus.ERROR,
                        raw_result=f"Error: {r}",
                        washed_result=None,
                        error_message=str(r),
                        elapsed_ms=None,
                    )
                )
            else:
                executed.append(r)
        return executed

    async def _execute_tool_calls(
        self,
        planned_calls: list[PlannedToolCall],
    ) -> list[ExecutedToolResult]:
        """允许的工具按 ``concurrency_safe`` 分批调度；拒绝项仅物化结果。"""
        denied_out, allowed_plans = self._split_planned_denied_allowed(
            planned_calls
        )

        if not allowed_plans:
            logger.info(
                "[Tool] skip (no allowed tools executed; denied-only or empty)"
            )
            return denied_out

        logger.info(
            "[Tool] begin partitioned allowed=%s tools=%s",
            len(allowed_plans),
            [p.tool_name for p in allowed_plans],
        )
        registry = get_tool_registry()
        executed_allowed: list[ExecutedToolResult] = []
        for batch in partition_tool_calls(allowed_plans, registry):
            executed_allowed.extend(
                await self._execute_tool_plan_batch(batch)
            )

        logger.info(
            "[Tool] done results=%s",
            len(allowed_plans),
        )
        return denied_out + executed_allowed

    def _wash_context_for_intent(self) -> str:
        """
        捕获「调用工具前」路由可见的对话上下文（不含当前 probe 的 assistant 条）。
        使用最近两轮 user/assistant 交互（至多 4 条），单条过长时截断。
        """
        blocks: list[str] = []
        for m in self.messages[1:]:
            if m.role not in ("user", "assistant"):
                continue
            text = (m.content or "").strip()
            if len(text) > _WASH_CONTEXT_PER_MSG_CAP:
                text = (
                    text[:_WASH_CONTEXT_PER_MSG_CAP]
                    + "\n...[truncated]"
                )
            blocks.append(f"{m.role.upper()}:\n{text}")
        if not blocks:
            return "(no prior user/assistant dialogue before this probe)"
        recent = blocks[-_WASH_CONTEXT_MAX_MESSAGES:]
        return "\n\n---\n\n".join(recent)

    async def _wash_tool_result(
        self,
        tool_name: str,
        tool_args: str,
        raw_result: str,
        context: str,
    ) -> str:
        """
        Intent-Driven Dynamic Wash：结合路由意图过滤工具噪声，始终经 Cognitive Filter（无字数门槛）。
        失败时降级为硬截断 + 后缀。
        """
        washer_sys = (
            "You are the Cognitive Filter of Project Chimera. Your job is to extract ONLY the "
            "information necessary to fulfill the Agent's recent tool invocation, while aggressively "
            "discarding noise. \n\n"
            f"The Agent recently decided to call the tool `{tool_name}` with args `{tool_args}` "
            f"based on this context:\n<CONTEXT>\n{context}\n</CONTEXT>\n\n"
            "Here is the raw output from the tool:\n<RAW_OUTPUT>\n"
            f"{raw_result}\n</RAW_OUTPUT>\n\n"
            "Extract the facts, numbers, or conclusions relevant to the context. Do NOT write an essay. "
            "If the raw output contains NOTHING relevant to the context, output exactly: "
            "'[Wash Result]: No relevant information found.' Otherwise, output the dense, factual summary."
        )
        compress_client = self.wash_client or self.llm_client
        x_len = len(raw_result)
        logger.info(
            "[Wash] intent_wash begin tool=%s raw_chars=%s backend=%s",
            tool_name,
            x_len,
            "wash_client" if self.wash_client is not None else "llm_client",
        )
        wash_messages: list[dict[str, str]] = [
            {"role": "system", "content": washer_sys},
            {
                "role": "user",
                "content": "Output the Cognitive Filter result only (no preamble).",
            },
        ]
        try:
            washed = await compress_client.generate_raw_text(wash_messages)
            out = str(washed)
            logger.info(
                "[Wash] ok %s chars -> %s chars", x_len, len(out)
            )
            self._maybe_record_wash(len(raw_result), len(out), tool_name)
            return out
        except CLIENT_GONE_EXCEPTIONS:
            raise
        except Exception:
            logger.warning(
                "[Wash] failed, degrading to hard truncation.",
            )
            degraded = raw_result[:_WASH_FALLBACK_CHARS] + _WASH_TRUNC_SUFFIX
            logger.info(
                "[Wash] degraded %s chars -> %s chars",
                x_len,
                len(degraded),
            )
            self._maybe_record_wash(len(raw_result), len(degraded), tool_name)
            return degraded

    async def _wash_tool_results(
        self,
        results: list[ExecutedToolResult],
        context: str,
    ) -> tuple[list[ExecutedToolResult], list[tuple[str, int]]]:
        """Apply minimal per-tool wash policy; LLM wash only when rules say so.

        Returns:
            Updated results and a list of (tool_name, raw_char_count) for each
            invocation that ran the LLM Cognitive Filter (true wash).
        """
        out: list[ExecutedToolResult] = []
        real_washes: list[tuple[str, int]] = []
        for er in results:
            raw_text = er.raw_result or ""
            can_llm_wash = (
                er.status == ToolCallStatus.SUCCESS and raw_text.strip() != ""
            )
            if not can_llm_wash:
                out.append(
                    er.model_copy(update={"washed_result": er.raw_result})
                )
                continue

            tool_name = er.tool_name
            cfg = self._agent_config
            if tool_name in cfg.bypass_wash_tools:
                washed = raw_text
            elif (
                tool_name in cfg.force_wash_tools
                and len(raw_text) >= cfg.wash_min_chars
            ):
                tool_args = json.dumps(er.args, ensure_ascii=False)
                raw_char_count = len(raw_text)
                washed = await self._wash_tool_result(
                    tool_name,
                    tool_args,
                    raw_text,
                    context,
                )
                real_washes.append((tool_name, raw_char_count))
            else:
                washed = raw_text

            out.append(er.model_copy(update={"washed_result": washed}))
        return out, real_washes

    def _render_tool_results_for_llm(
        self,
        results: list[ExecutedToolResult],
    ) -> str:
        """Format executed tool rows into one stable user message (no LLM calls)."""
        parts: list[str] = ["[SYSTEM TOOL RESULTS]", ""]
        for er in results:
            payload = er.washed_result or er.raw_result or er.error_message
            if payload is None or str(payload).strip() == "":
                result_body = "[No content]"
            else:
                result_body = str(payload)
            parts.extend(
                [
                    f"--- Tool Call {er.call_id} ---",
                    f"Tool: {er.tool_name}",
                    f"Status: {er.status.name}",
                    f"Args: {json.dumps(er.args, ensure_ascii=False)}",
                    "Result:",
                    result_body,
                    "",
                ]
            )
        parts.extend(
            [
                "Instruction:",
                "Synthesize the results above. If sufficient evidence is present, produce the final answer.",
                "Do NOT call more tools unless the results are clearly insufficient.",
            ]
        )
        return "\n".join(parts)

    async def _run_theater_stream(self) -> AsyncGenerator[str, None]:
        """Core theater loop; client-gone and pipe-broken handling live in ``run_theater`` only."""
        turn = 0

        while turn < self.max_turns:
            turn += 1
            logger.debug(f"[Oligo] Theater turn {turn}/{self.max_turns}")

            # ---------- 步骤 A: 闭门思考（非流式！）----------
            logger.info(
                "[Router] probe_begin turn=%s/%s", turn, self.max_turns
            )
            if self.messages:
                preview = self.messages[0].content[:1000] + (
                    "..." if len(self.messages[0].content) > 1000 else ""
                )
                logger.info(
                    "[Router] ROUTER SYS (first 1000 chars): %s",
                    preview[:1000],
                )
            self._apply_history_sanitizer_to_messages()
            api_messages = _messages_to_api(self.messages)
            try:
                probe_response = await asyncio.wait_for(
                    self._router_client.generate_raw_text(api_messages),
                    timeout=_THEATER_LLM_OUTER_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[Router] LLM gateway timeout (router probe, %.0fs watchdog)",
                    _THEATER_LLM_OUTER_TIMEOUT_S,
                )
                yield sse_event(
                    "bb-stream-done",
                    {"error": True, "message": "LLM gateway timeout"},
                )
                return

            # ---------- 步骤 B: 检查结果 ----------
            logger.info("[Router] Full response (probe): %s", probe_response)
            planned_calls = self._parse_tool_calls(probe_response)
            logger.info(
                "[Router] probe_end tool_calls=%s", len(planned_calls)
            )

            if len(planned_calls) > 0:
                yield _sse_data(
                    _sys_telemetry_obj(
                        {
                            "stage": "router",
                            "content": f"{len(planned_calls)} tool calls planned",
                            "decision": "parallel",
                            "parallel_count": len(planned_calls),
                        }
                    )
                )

                for plan in planned_calls:
                    tool_name = plan.tool_name
                    tool_args = plan.raw_args
                    logger.info(
                        "[Router] Intercepted Raw Args from LLM: %r",
                        tool_args,
                    )

                    tel_router: dict[str, Any] = {
                        "stage": "router",
                        "content": f"Planning {tool_name}",
                        "tool_name": tool_name,
                        "decision": tool_name,
                        "raw_args": tool_args,
                    }
                    if plan.repairs_applied:
                        tel_router["args_repaired"] = True
                        tel_router["repairs_applied"] = list(plan.repairs_applied)
                    yield _sse_data(_sys_telemetry_obj(tel_router))

                    if not plan.allowed:
                        yield _sse_data(
                            _sys_telemetry_obj(
                                {
                                    "stage": "tool",
                                    "content": f"Denied: {tool_name}",
                                    "tool_name": tool_name,
                                    "deny_reason": (plan.deny_reason or ""),
                                }
                            )
                        )

                wash_context = self._wash_context_for_intent()

                denied_out, allowed_plans = self._split_planned_denied_allowed(
                    planned_calls
                )
                executed_allowed: list[ExecutedToolResult] = []
                if allowed_plans:
                    logger.info(
                        "[Tool] begin partitioned allowed=%s tools=%s",
                        len(allowed_plans),
                        [p.tool_name for p in allowed_plans],
                    )
                    _reg = get_tool_registry()
                    for _batch in partition_tool_calls(allowed_plans, _reg):
                        yield _sse_data(
                            _sys_telemetry_obj(
                                _tool_batch_start_payload(_batch)
                            )
                        )
                        executed_allowed.extend(
                            await self._execute_tool_plan_batch(_batch)
                        )
                    logger.info(
                        "[Tool] done results=%s",
                        len(allowed_plans),
                    )
                else:
                    logger.info(
                        "[Tool] skip (no allowed tools executed; denied-only or empty)"
                    )
                executed_results = denied_out + executed_allowed

                for er in executed_results:
                    yield _sse_data(
                        _sys_telemetry_obj(
                            {
                                "stage": "tool",
                                "content": f"{er.tool_name} → {er.status.name}",
                                "tool_name": er.tool_name,
                                "execution_status": er.status.name,
                            }
                        )
                    )

                executed_results, wash_events = await self._wash_tool_results(
                    executed_results, wash_context
                )

                if not wash_events:
                    yield _sse_data(
                        _sys_telemetry_obj(
                            {
                                "stage": "wash",
                                "content": "All tool results bypassed wash (under threshold).",
                            }
                        )
                    )

                for tool_name_w, raw_chars in wash_events:
                    yield _sse_data(
                        _sys_telemetry_obj(
                            {
                                "stage": "wash",
                                "content": f"{tool_name_w}: {raw_chars} chars",
                                "tool_name": tool_name_w,
                                "raw_chars": raw_chars,
                            }
                        )
                    )

                probe_for_cmd = _strip_markdown_code_for_cmd_extraction(
                    probe_response
                )
                cmd_only = extract_tool_syntax_for_history(probe_for_cmd)
                _cmd_len = len(cmd_only)
                if _cmd_len > 8000:
                    cmd_only = (
                        f"{cmd_only[:8000]}\n...[truncated {_cmd_len - 8000} chars]"
                    )
                self.messages.append(
                    ChatMessage(role="assistant", content=cmd_only)
                )

                logger.info(
                    "[Wash] aggregate tool_results=%s",
                    len(executed_results),
                )

                tool_result_message = self._render_tool_results_for_llm(
                    executed_results
                )
                self.messages.append(
                    ChatMessage(role="user", content=tool_result_message)
                )

                continue

            backfill_draft = TextSanitizer.strip_tool_syntax_in_visible(
                probe_response
            )
            _probe_trivial = _is_router_pass_or_trivial(backfill_draft)
            if not _probe_trivial:
                self.messages.append(
                    ChatMessage(role="assistant", content=backfill_draft)
                )
                logger.info(
                    "[Router] probe_draft_backfill chars=%s (raw_len=%s)",
                    len(backfill_draft),
                    len(probe_response or ""),
                )

            yield _sse_data(
                _sys_telemetry_obj(
                    {
                        "stage": "router",
                        "decision": "pass",
                        "content": (
                            "Router decided no tools; draft backfilled for Final."
                            if not _probe_trivial
                            else "Router decided no tools are needed."
                        ),
                    }
                )
            )

            # ---------- 步骤 C: 晚期绑定 + 终极推流 ----------
            logger.info("[Final] begin (persona bind + generate buffer)")
            self._apply_history_sanitizer_to_messages()
            final_system = ChatMessage(
                role="system",
                content=self._final_persona_system_content(),
            )
            tail = [m.model_copy(deep=True) for m in self.messages[1:]]
            final_messages: list[ChatMessage] = [final_system, *tail]

            fs_preview = final_system.content[:1000] + (
                "..." if len(final_system.content) > 1000 else ""
            )
            logger.info(
                "[Final] FINAL PERSONA SYS (first 150 chars): %s",
                fs_preview[:150],
            )

            try:
                full_response = await asyncio.wait_for(
                    self.llm_client.generate_raw_text(
                        _messages_to_api(final_messages)
                    ),
                    timeout=_THEATER_LLM_OUTER_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[Final] LLM gateway timeout (final stream buffer, %.0fs watchdog)",
                    _THEATER_LLM_OUTER_TIMEOUT_S,
                )
                yield sse_event(
                    "bb-stream-done",
                    {"error": True, "message": "LLM gateway timeout"},
                )
                return

            logger.info("[Final] Full response (final stream): %s", full_response)
            logger.info(
                "[Final] buffer_ready chars=%s sse_chunking",
                len(full_response),
            )

            yield _sse_data(
                _sys_telemetry_obj(
                    {
                        "stage": "final",
                        "content": "Generating final response…",
                    }
                )
            )

            # 位点 A：整段先经层 1+2 再分块，避免 <thinking>/<CMD> 被 chunk 边界切开
            stream_body = TextSanitizer.strip_tool_syntax_in_visible(
                TextSanitizer.strip_reasoning_tags(full_response)
            )
            chunk_size = 3
            for i in range(0, len(stream_body), chunk_size):
                chunk = stream_body[i : i + chunk_size]
                yield _sse_chunk(chunk)
                await asyncio.sleep(0.04)

            logger.debug(f"[Oligo] Theater concluded on turn {turn}.")
            return

        # ---------- 步骤 D: 耗尽回合，Fallback ----------
        error_msg = (
            "\n\n[SYSTEM FATAL]: Agent exhausted max turns. Shutting down."
        )
        logger.error("[Oligo] Fallback: %s", error_msg)
        yield sse_event("bb-stream-done", {"error": True, "message": error_msg.strip()})

    async def run_theater(self) -> AsyncGenerator[str, None]:
        """
        剧场版主循环：闭门思考 → 检查 CMD → 终极推流。

        客户端断连类异常仅在**本方法**最外层统一记录并下发 ``bb-stream-done`` 信标；
        内层 ``_run_theater_stream`` 与各 helper 对 ``CLIENT_GONE_EXCEPTIONS`` 一律原样上抛。
        """
        try:
            async for chunk in self._run_theater_stream():
                yield chunk
        except CLIENT_GONE_EXCEPTIONS:
            _handle_client_gone()
            yield sse_event("bb-stream-done", {"aborted": True, "reason": "client_gone"})
            return
        except Exception as exc:
            if _looks_like_pipe_broken(exc):
                _handle_client_gone()
                yield sse_event("bb-stream-done", {"aborted": True, "reason": "client_gone"})
                return
            raise


# --- TEST HARNESS ---
if __name__ == "__main__":
    import asyncio

    from pydantic import BaseModel

    from src.oligo.tools.vault_tools import set_vault_adapter

    class _HarnessVault:
        async def search_notes(self, query: str, top_k: int = 3) -> str:
            return f"[HarnessVault] snippets for {query!r}"

        async def search_by_attribute(self, key: str, value: str, top_k: int = 5) -> str:
            return f"[HarnessVault] attr {key}={value!r}"

        async def query_graph(
            self,
            node_type: str | None = None,
            link_pattern: str | None = None,
            max_depth: int = 2,
        ) -> list[dict[str, Any]]:
            return []

    class MockLLMClient:
        """无 API 调用的测试客户端（与 ``LLMClient`` 结构兼容）。"""

        async def generate_raw_text(self, messages: list[dict[str, str]]) -> str:
            full_conv = " ".join(m.get("content", "") for m in messages)
            sys0 = messages[0].get("content", "") if messages else ""
            if "Chimera OS local router" in sys0:
                if "[SYSTEM TOOL RESULTS]" in full_conv:
                    return "Senpai, based on the vault: Titans is flawed. That is all."
                return '<CMD:search_vault({"query": "Titans"})> Searching...'
            if "[SYSTEM TOOL RESULTS]" in full_conv:
                return "Senpai, based on the vault: Titans is flawed. That is all."
            return "Hello from BB."

        async def stream_generate(
            self, messages: list[dict[str, str]]
        ) -> AsyncGenerator[str, None]:
            for c in "Senpai, Titans is flawed. That is all.":
                yield c
                await asyncio.sleep(0.03)

        async def generate_structured_data_async(
            self,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> BaseModel:
            raise NotImplementedError("MockLLMClient does not support structured output")

    async def test_run():
        set_vault_adapter(_HarnessVault())
        try:
            agent = ChimeraAgent(
                raw_messages=[{"role": "user", "content": "Fetch Titans."}],
                system_core="You are BB, a dramatic waifu persona.",
                skill_override=None,
                llm_client=MockLLMClient(),
            )
            print("Frontend receives:", end="", flush=True)
            async for chunk in agent.run_theater():
                print(chunk, end="", flush=True)
            print("\n\nDone.")
        finally:
            set_vault_adapter(None)

    asyncio.run(test_run())
