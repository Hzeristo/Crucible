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
import uuid
from typing import AsyncGenerator, Any

from src.crucible.core.schemas import (
    ChatMessage,
    ExecutedToolResult,
    OligoAgentConfig,
    PlannedToolCall,
    ToolCallStatus,
)
from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient
from src.crucible.ports.vault.vault_read_adapter import VaultReadAdapter
from src.oligo.tools import TOOL_REGISTRY

logger = logging.getLogger(__name__)

CLIENT_SEVERED_WARNING = (
    "[Oligo Core: FATAL] Client forcibly severed the connection. "
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

# 捕获 <CMD:tool_name(args)> 格式
CMD_REGEX = re.compile(r"<CMD:([a-zA-Z0-9_]+)\((.*?)\)>", re.DOTALL)

_TOOL_TIMEOUT_MESSAGE = (
    "[TOOL TIMEOUT]: The execution exceeded 45 seconds and was terminated by the Overseer."
)
_SSE_ABORT_STREAM_DONE = 'bb-stream-done: {"aborted": true}'
# Intent-Driven Wash: fallback when LLM call fails (chars + suffix length budget)
_WASH_FALLBACK_CHARS = 1500
_WASH_TRUNC_SUFFIX = "...[TRUNCATED]"
# Router-visible dialogue tail: last two user/assistant rounds (max 4 messages)
_WASH_CONTEXT_MAX_MESSAGES = 4
_WASH_CONTEXT_PER_MSG_CAP = 8000

# 工具环专用：极短路由 System，与晚期绑定的 BB / skill 人设分离。
_ROUTER_SYSTEM_PROMPT = (
    "You are the Chimera OS local router. Your job is to analyze the user's input and determine if "
    "it requires pulling information from the Obsidian Vault to provide an accurate answer.\n\n"
    "1. IF (and ONLY if) the user asks a specific research question, refers to past papers, or requires "
    "data not in the immediate chat context, choose ONE vault tool and output exactly ONE <CMD>:\n"
    '   - Full-text keyword search (grep-style over note bodies): '
    '<CMD:search_vault({"query": "precise keywords"})>\n'
    "   - Structured YAML frontmatter search (match a frontmatter key; value may be a string or list "
    "whose string form contains the given substring): "
    '<CMD:search_vault_attribute({"key": "metadata_field", "value": "substring"})>\n'
    "2. IF the user is just saying hello, making casual conversation, or giving an instruction that "
    'requires no external data, YOU MUST NOT output any <CMD>. Instead, output exactly the word: <PASS>\n\n'
    "DO NOT roleplay. DO NOT be polite. Just output either the <CMD...> or <PASS>. Nothing else."
)



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


def _parse_cmd_tool_args(raw_args: str) -> dict[str, Any]:
    """
    解析工具参数为合法 JSON Object。

    Args:
        raw_args: 原始参数字符串，应为 JSON 对象如 {"query": "..."}。

    Returns:
        解析后的字典。

    Raises:
        json.JSONDecodeError: 非法 JSON 格式。
        ValueError: 解析结果既不是 dict 也不是可宽容的 str。
    """
    data = json.loads(raw_args.strip())
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        logger.warning(
            f"[Oligo Core: Parser] LLM failed to output JSON Object. "
            f"Coercing raw string to {{'query': {data!r}}}"
        )
        return {"query": data}
    raise ValueError(f"Expected JSON object, got {type(data).__name__}")


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
    1. 工具搜寻环：使用硬编码路由 System（可选追加 ``[Skill Directives]``）+ 纯净
       ``raw_messages``；非流式 ``generate_raw_text`` 探包，避免 ``<CMD>`` 截断。
    2. 晚期绑定：一旦本轮回答不含 ``<CMD>``，丢弃路由 System，将 ``system_core`` 与
       ``skill_override`` 拼成最终 System 置于历史顶端（含已注入的
       ``[SYSTEM TOOL RESULTS]``），再发起**最后一次** ``generate_raw_text``，再推流。
    3. 边界处通过 ``model_dump`` 与网络层对接；异常与 SSE 帧格式保持不变。
    """

    def __init__(
        self,
        raw_messages: list[dict[str, Any]] | list[ChatMessage],
        system_core: str,
        skill_override: str | None,
        llm_client: Any,
        wash_client: OpenAICompatibleClient | None = None,
        max_turns: int = 5,
        allowed_tools: list[str] | None = None,
        vault: VaultReadAdapter | None = None,
        agent_config: OligoAgentConfig | None = None,
    ) -> None:
        """
        Args:
            raw_messages: 纯净 user/assistant 对话（无网关预拼 System）。
            system_core: 人设基座（如 BB），仅在最终入模前绑定。
            skill_override: 技能覆写文案；参与路由环 ``[Skill Directives]`` 与最终 System。
            llm_client: 需实现 ``generate_raw_text(messages: list[dict]) -> str``。
            wash_client: 可选廉价 OpenAI 兼容客户端，用于工具结果 Wash 压缩；缺省时回落到 ``llm_client``。
            max_turns: 最大 ReAct 轮次。
            allowed_tools: 可执行工具白名单；为 None 时不做限制。
            agent_config: 工具超时与 wash 策略；缺省使用内置 ``OligoAgentConfig()``。
        """
        self._system_core = system_core
        self._skill_override = (skill_override or "").strip() or None
        self.allowed_tools = allowed_tools
        self._agent_config = agent_config or OligoAgentConfig()

        self.raw_messages: list[ChatMessage] = _ensure_chat_messages(raw_messages)
        self.llm_client = llm_client
        self.wash_client = wash_client
        self.max_turns = max_turns
        self._vault = vault

        router_body = _ROUTER_SYSTEM_PROMPT
        if self._skill_override:
            router_body = (
                f"{router_body}\n\n"
                "[USER SKILL DIRECTIVE (FOLLOW THIS FOR YOUR REASONING)]:\n"
                f"{self._skill_override}"
            )

        self.messages: list[ChatMessage] = [
            ChatMessage(role="system", content=router_body),
            *[m.model_copy(deep=True) for m in self.raw_messages],
        ]

    def _final_persona_system_content(self) -> str:
        """与 API 层「三明治」规则一致：core + 可选 override，统治力拼接。"""
        core = self._system_core.rstrip()
        override = self._skill_override or ""
        if override:
            return f"{core}\n\n{override}" if core else override
        return core

    def _parse_tool_args(self, raw_args: str) -> dict[str, Any]:
        """Parse CMD parenthesis JSON; delegates to module-level ``_parse_cmd_tool_args``."""
        return _parse_cmd_tool_args(raw_args)

    def _parse_tool_calls(self, probe_response: str) -> list[PlannedToolCall]:
        """Parse ``<CMD:...>`` tags into structured plans with allowlist resolution."""
        planned: list[PlannedToolCall] = []
        for match in CMD_REGEX.finditer(probe_response):
            tool_name = match.group(1)
            raw_args = match.group(2)
            try:
                args = self._parse_tool_args(raw_args)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "[Oligo Core: Parser] CMD args parse failed for tool=%s: %s; "
                    "using empty args dict (execute step will re-validate).",
                    tool_name,
                    e,
                )
                args = {}
            if self.allowed_tools is None:
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
                    id=uuid.uuid4().hex[:12],
                    tool_name=tool_name,
                    raw_args=raw_args,
                    args=args,
                    allowed=allowed,
                    deny_reason=deny_reason,
                )
            )
        return planned

    async def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        """
        从 TOOL_REGISTRY 或 Vault 调度工具执行；入参为 planning 阶段已解析的 dict。

        Returns:
            工具执行结果字符串；出错时返回 "Error: ..." 描述。
        """
        if tool_name == "search_vault" and self._vault is not None:
            try:
                q = str(args.get("query", "")).strip()
                top_k = int(args.get("top_k", 3))
                out = await self._vault.search_notes(q, top_k)
            except (TypeError, ValueError) as e:
                out = f"Error: Tool 'search_vault' invalid args: {e}"
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out

        if tool_name == "search_vault_attribute" and self._vault is not None:
            try:
                attr_key = str(args.get("key", "")).strip()
                attr_val = str(args.get("value", "")).strip()
                top_k = int(args.get("top_k", 5))
                out = await self._vault.search_by_attribute(attr_key, attr_val, top_k)
            except (TypeError, ValueError) as e:
                out = f"Error: Tool 'search_vault_attribute' invalid args: {e}"
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out

        if tool_name not in TOOL_REGISTRY:
            out = f"Error: Tool '{tool_name}' is not recognized by the Chimera OS."
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out
        fn = TOOL_REGISTRY[tool_name]
        try:
            result = await fn(**args)
            out = str(result)
        except TypeError as e:
            out = f"Error: Tool '{tool_name}' invalid args: {e}"
        logger.info(
            f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
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
                "[Oligo Core] Tool deadline exceeded (%.0fs): tool=%s",
                deadline,
                tool_name,
            )
            return _TOOL_TIMEOUT_MESSAGE

    async def _execute_tool_calls(
        self,
        planned_calls: list[PlannedToolCall],
    ) -> list[ExecutedToolResult]:
        """Run allowed tools concurrently with per-call deadline; denied calls are materialized only."""
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

        if not allowed_plans:
            logger.info(
                "[Tool Execution] skip (no allowed tools executed; "
                "denied-only or empty)"
            )
            return denied_out

        logger.info(
            "[Tool Execution] begin parallel=%s tools=%s",
            len(allowed_plans),
            [p.tool_name for p in allowed_plans],
        )

        async def _run_one(plan: PlannedToolCall) -> ExecutedToolResult:
            t0 = time.perf_counter()
            try:
                raw = await self._execute_tool_with_deadline(
                    plan.tool_name, plan.args
                )
            except BaseException as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                if isinstance(exc, CLIENT_GONE_EXCEPTIONS):
                    raise
                return ExecutedToolResult(
                    call_id=plan.id,
                    tool_name=plan.tool_name,
                    args=plan.args,
                    status=ToolCallStatus.ERROR,
                    raw_result=f"Error: {exc}",
                    washed_result=None,
                    error_message=str(exc),
                    elapsed_ms=elapsed_ms,
                )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if raw == _TOOL_TIMEOUT_MESSAGE:
                return ExecutedToolResult(
                    call_id=plan.id,
                    tool_name=plan.tool_name,
                    args=plan.args,
                    status=ToolCallStatus.TIMEOUT,
                    raw_result=raw,
                    washed_result=None,
                    error_message=_TOOL_TIMEOUT_MESSAGE,
                    elapsed_ms=elapsed_ms,
                )
            return ExecutedToolResult(
                call_id=plan.id,
                tool_name=plan.tool_name,
                args=plan.args,
                status=ToolCallStatus.SUCCESS,
                raw_result=str(raw),
                washed_result=None,
                error_message=None,
                elapsed_ms=elapsed_ms,
            )

        results = await asyncio.gather(
            *(_run_one(p) for p in allowed_plans),
            return_exceptions=True,
        )
        executed_allowed: list[ExecutedToolResult] = []
        for plan, r in zip(allowed_plans, results):
            if isinstance(r, BaseException):
                if isinstance(r, CLIENT_GONE_EXCEPTIONS):
                    raise r
                executed_allowed.append(
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
                executed_allowed.append(r)

        logger.info(
            "[Tool Execution] done results=%s",
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
            "[Wash Compression] intent_wash begin tool=%s raw_chars=%s backend=%s",
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
                "[Wash Compression] ok %s chars -> %s chars", x_len, len(out)
            )
            return out
        except CLIENT_GONE_EXCEPTIONS:
            raise
        except Exception:
            logger.warning(
                "[Wash Compression] failed, degrading to hard truncation.",
            )
            degraded = raw_result[:_WASH_FALLBACK_CHARS] + _WASH_TRUNC_SUFFIX
            logger.info(
                "[Wash Compression] degraded %s chars -> %s chars",
                x_len,
                len(degraded),
            )
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
            logger.debug(f"[Oligo Core] Theater turn {turn}/{self.max_turns}")

            # ---------- 步骤 A: 闭门思考（非流式！）----------
            logger.info(
                "[Router] probe_begin turn=%s/%s", turn, self.max_turns
            )
            if self.messages:
                preview = self.messages[0].content[:1000] + (
                    "..." if len(self.messages[0].content) > 1000 else ""
                )
                logger.info(
                    "==> [Oligo Core] ROUTER SYS (first 1000 chars): %s",
                    preview[:1000],
                )
            api_messages = _messages_to_api(self.messages)
            probe_response = await self.llm_client.generate_raw_text(
                api_messages
            )

            # ---------- 步骤 B: 检查结果 ----------
            logger.info(f"[Oligo Core] Full response (probe): {probe_response}")
            planned_calls = self._parse_tool_calls(probe_response)
            logger.info(
                "[Router] probe_end tool_calls=%s", len(planned_calls)
            )

            if len(planned_calls) > 0:
                yield _sse_data(
                    f"__SYS_TOOL_CALL__parallel::{len(planned_calls)} tool calls planned"
                )

                for plan in planned_calls:
                    tool_name = plan.tool_name
                    tool_args = plan.raw_args
                    logger.info(
                        f"[X-RAY] Intercepted Raw Args from LLM: {tool_args!r}"
                    )

                    yield _sse_data(f"__SYS_TOOL_CALL__{tool_name}::{tool_args}")

                    if not plan.allowed:
                        yield _sse_data(
                            f"__SYS_TOOL_CALL__denied::{tool_name}"
                        )

                wash_context = self._wash_context_for_intent()

                executed_results = await self._execute_tool_calls(planned_calls)

                for er in executed_results:
                    yield _sse_data(
                        f"__SYS_TOOL_CALL__completed::{er.tool_name}::{er.status.name}"
                    )

                executed_results, wash_events = await self._wash_tool_results(
                    executed_results, wash_context
                )

                for tool_name_w, raw_chars in wash_events:
                    yield _sse_data(
                        f"__SYS_TOOL_CALL__wash::{tool_name_w}::{raw_chars} chars"
                    )

                cmd_only = "\n".join(
                    m.group(0) for m in CMD_REGEX.finditer(probe_response)
                )
                _cmd_len = len(cmd_only)
                if _cmd_len > 8000:
                    cmd_only = (
                        f"{cmd_only[:8000]}\n...[truncated {_cmd_len - 8000} chars]"
                    )
                self.messages.append(
                    ChatMessage(role="assistant", content=cmd_only)
                )

                logger.info(
                    "[Wash Compression] aggregate tool_results=%s",
                    len(executed_results),
                )

                tool_result_message = self._render_tool_results_for_llm(
                    executed_results
                )
                self.messages.append(
                    ChatMessage(role="user", content=tool_result_message)
                )

                continue

            # ---------- 步骤 C: 晚期绑定 + 终极推流 ----------
            logger.info("[Final Stream] begin (persona bind + generate buffer)")
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
                "==> [Oligo Core] FINAL PERSONA SYS (first 150 chars): %s",
                fs_preview[:150],
            )

            full_response = await self.llm_client.generate_raw_text(
                _messages_to_api(final_messages)
            )

            logger.info(f"[Oligo Core] Full response (final stream): {full_response}")
            logger.info(
                "[Final Stream] buffer_ready chars=%s sse_chunking",
                len(full_response),
            )

            chunk_size = 3
            for i in range(0, len(full_response), chunk_size):
                chunk = full_response[i : i + chunk_size]
                yield _sse_data(chunk)
                await asyncio.sleep(0.04)

            logger.debug(f"[Oligo Core] Theater concluded on turn {turn}.")
            return

        # ---------- 步骤 D: 耗尽回合，Fallback ----------
        error_msg = (
            "\n\n[SYSTEM FATAL]: Agent exhausted max turns. Shutting down."
        )
        logger.error("[Oligo Core: Fallback] %s", error_msg)
        yield _sse_data(error_msg)

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
            yield _sse_data(_SSE_ABORT_STREAM_DONE)
            return
        except Exception as exc:
            if _looks_like_pipe_broken(exc):
                _handle_client_gone()
                yield _sse_data(_SSE_ABORT_STREAM_DONE)
                return
            raise


# --- TEST HARNESS ---
if __name__ == "__main__":
    import asyncio

    class MockLLMClient:
        """无 API 调用的测试客户端。"""

        async def generate_raw_text(self, messages: list[dict]) -> str:
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
            self, messages: list[dict]
        ) -> AsyncGenerator[str, None]:
            for c in "Senpai, Titans is flawed. That is all.":
                yield c
                await asyncio.sleep(0.03)

    async def test_run():
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

    asyncio.run(test_run())
