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
from typing import AsyncGenerator, Any

from src.oligo.domain.schemas import ChatMessage
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

# 工具环专用：极短路由 System，与晚期绑定的 BB / skill 人设分离。
_ROUTER_SYSTEM_PROMPT = (
    "You are the Chimera OS local router. Your job is to analyze the user's input and determine if "
    "it requires pulling information from the Obsidian Vault to provide an accurate answer.\n\n"
    "1. IF (and ONLY if) the user asks a specific research question, refers to past papers, or requires "
    'data not in the immediate chat context, output exactly: <CMD:search_vault({"query": "precise keywords"})>\n'
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


def _parse_tool_args(raw_args: str) -> dict[str, Any]:
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
       ``[SYSTEM TOOL RESULT]``），再发起**最后一次** ``generate_raw_text``，再推流。
    3. 边界处通过 ``model_dump`` 与网络层对接；异常与 SSE 帧格式保持不变。
    """

    def __init__(
        self,
        raw_messages: list[dict[str, Any]] | list[ChatMessage],
        system_core: str,
        skill_override: str | None,
        llm_client: Any,
        max_turns: int = 5,
        allowed_tools: list[str] | None = None,
    ) -> None:
        """
        Args:
            raw_messages: 纯净 user/assistant 对话（无网关预拼 System）。
            system_core: 人设基座（如 BB），仅在最终入模前绑定。
            skill_override: 技能覆写文案；参与路由环 ``[Skill Directives]`` 与最终 System。
            llm_client: 需实现 ``generate_raw_text(messages: list[dict]) -> str``。
            max_turns: 最大 ReAct 轮次。
            allowed_tools: 可执行工具白名单；为 None 时不做限制。
        """
        self._system_core = system_core
        self._skill_override = (skill_override or "").strip() or None
        self.allowed_tools = allowed_tools

        self.raw_messages: list[ChatMessage] = _ensure_chat_messages(raw_messages)
        self.llm_client = llm_client
        self.max_turns = max_turns

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

    async def _execute_tool(self, tool_name: str, raw_args: str) -> str:
        """
        从 TOOL_REGISTRY 调度工具执行。

        Args:
            tool_name: 工具名称，必须在 TOOL_REGISTRY 中注册。
            raw_args: 原始参数字符串，必须为合法 JSON Object。

        Returns:
            工具执行结果字符串；出错时返回 "Error: ..." 描述。
        """
        if tool_name not in TOOL_REGISTRY:
            out = f"Error: Tool '{tool_name}' is not recognized by the Chimera OS."
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out
        fn = TOOL_REGISTRY[tool_name]
        try:
            args_dict = _parse_tool_args(raw_args)
        except json.JSONDecodeError as e:
            out = (
                f"Error: Invalid tool args. Must be JSON object e.g. {{\"query\": \"...\"}}. "
                f"You sent: {raw_args!r}. Parse error: {e}"
            )
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out
        except ValueError as e:
            out = f"Error: {e}"
            logger.info(
                f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
            )
            return out
        try:
            result = await fn(**args_dict)
            out = str(result)
        except TypeError as e:
            out = f"Error: Tool '{tool_name}' invalid args: {e}"
        logger.info(
            f"[X-RAY] Tool '{tool_name}' returned (first 300 chars): {out[:300]!r}"
        )
        return out

    async def _wash_tool_result(self, raw_result: str, max_chars: int = 1500) -> str:
        """
        对超长工具输出做抗熵压缩，避免撑爆上下文窗口。

        Args:
            raw_result: 原始工具输出文本。
            max_chars: 不触发压缩的字符阈值。

        Returns:
            压缩后文本；若压缩失败则回退为原文截断。
        """
        if len(raw_result) <= max_chars:
            return raw_result

        wash_messages = [
            {
                "role": "system",
                "content": (
                    "You are a compression engine. Summarize the following raw text "
                    "into under 300 words. Preserve all factual claims, numbers, and "
                    "proper nouns. Output ONLY the summary."
                ),
            },
            {"role": "user", "content": raw_result},
        ]
        try:
            washed = await self.llm_client.generate_raw_text(wash_messages)
            return str(washed)
        except CLIENT_GONE_EXCEPTIONS:
            _handle_client_gone()
            raise
        except Exception:
            logger.warning("[Wash] Compression failed, degrading to truncation.")
            return raw_result[:max_chars]

    async def run_theater(self) -> AsyncGenerator[str, None]:
        """
        剧场版主循环：闭门思考 → 检查 CMD → 终极推流。

        步骤说明：
        A) 路由环：短 System + 历史，非流式 ``generate_raw_text`` 探包。
        B) 有 ``<CMD>``：执行工具、注入结果、continue。
        C) 无 ``<CMD>``：晚期绑定最终 System，再 ``generate_raw_text`` 一次，再 SSE 推流。
        D) turn > max_turns：Fallback，yield 错误信息并结束。

        客户端断连或任务取消时：单行 FATAL 警告后安静结束，不把 CancelledError
        继续抛给 ASGI（避免 Uvicorn 刷屏 traceback）。

        Yields:
            SSE 格式的 data 帧或系统事件字符串。
        """
        try:
            turn = 0

            while turn < self.max_turns:
                turn += 1
                logger.debug(f"[Oligo Core] Theater turn {turn}/{self.max_turns}")

                # ---------- 步骤 A: 闭门思考（非流式！）----------
                if self.messages:
                    preview = self.messages[0].content[:1000] + (
                        "..." if len(self.messages[0].content) > 1000 else ""
                    )
                    logger.info(
                        "==> [Oligo Core] ROUTER SYS (first 1000 chars): %s",
                        preview[:1000],
                    )
                api_messages = _messages_to_api(self.messages)
                try:
                    probe_response = await self.llm_client.generate_raw_text(
                        api_messages
                    )
                except CLIENT_GONE_EXCEPTIONS:
                    _handle_client_gone()
                    return

                # ---------- 步骤 B: 检查结果 ----------
                logger.info(f"[Oligo Core] Full response (probe): {probe_response}")
                matches = list(CMD_REGEX.finditer(probe_response))

                if len(matches) > 0:
                    try:
                        yield _sse_data(
                            f"__SYS_TOOL_CALL__parallel::{len(matches)} tools dispatched"
                        )
                    except CLIENT_GONE_EXCEPTIONS:
                        _handle_client_gone()
                        return

                    validated_tools: list[tuple[str, str]] = []
                    result_entries: list[tuple[str, str, str]] = []

                    for match in matches:
                        tool_name = match.group(1)
                        tool_args = match.group(2)
                        logger.info(
                            f"[X-RAY] Intercepted Raw Args from LLM: {tool_args!r}"
                        )

                        # 向下游发送系统工具调用信标，供 Rust/前端进行专用事件分流
                        try:
                            yield _sse_data(f"__SYS_TOOL_CALL__{tool_name}::{tool_args}")
                        except CLIENT_GONE_EXCEPTIONS:
                            _handle_client_gone()
                            return

                        if (
                            self.allowed_tools is not None
                            and tool_name not in self.allowed_tools
                        ):
                            denied_msg = (
                                f"[Permission Denied] Tool '{tool_name}' is not allowed "
                                "under current skill."
                            )
                            result_entries.append((tool_name, tool_args, denied_msg))
                            continue

                        validated_tools.append((tool_name, tool_args))

                    if validated_tools:
                        tasks = [
                            self._execute_tool(name, args)
                            for name, args in validated_tools
                        ]
                        try:
                            results = await asyncio.gather(
                                *tasks, return_exceptions=True
                            )
                        except CLIENT_GONE_EXCEPTIONS:
                            _handle_client_gone()
                            return

                        for (tool_name, tool_args), result in zip(
                            validated_tools, results
                        ):
                            normalized_result = (
                                f"Error: {result}"
                                if isinstance(result, BaseException)
                                else str(result)
                            )
                            try:
                                yield _sse_data(
                                    f"__SYS_TOOL_CALL__completed::{tool_name}"
                                )
                            except CLIENT_GONE_EXCEPTIONS:
                                _handle_client_gone()
                                return
                            result_entries.append(
                                (tool_name, tool_args, normalized_result)
                            )

                    # 将包含所有 <CMD> 的 probe 响应（可截断）作为 assistant 注入
                    assistant_probe = probe_response
                    if len(assistant_probe) > 8000:
                        assistant_probe = (
                            f"{assistant_probe[:8000]}\n...[truncated {len(probe_response) - 8000} chars]"
                        )
                    self.messages.append(
                        ChatMessage(role="assistant", content=assistant_probe)
                    )

                    # 聚合所有工具结果并注入
                    washed_blocks: list[str] = []
                    for tool_name, tool_args, raw_result in result_entries:
                        if len(str(raw_result)) > 1500:
                            try:
                                yield _sse_data(
                                    "__SYS_TOOL_CALL__wash::Compressing tool output..."
                                )
                            except CLIENT_GONE_EXCEPTIONS:
                                _handle_client_gone()
                                return
                        washed_result = await self._wash_tool_result(str(raw_result))
                        washed_blocks.append(
                            f"--- Result for {tool_name}({tool_args}) ---\n{washed_result}"
                        )
                    aggregated_results = (
                        "[SYSTEM TOOL RESULTS]\n"
                        f"{chr(10).join(washed_blocks)}\n"
                        "You MUST now synthesize ALL results above. "
                        "Do NOT call any more tools."
                    )
                    self.messages.append(
                        ChatMessage(role="user", content=aggregated_results)
                    )

                    continue

                # ---------- 步骤 C: 晚期绑定 + 终极推流 ----------
                # 抛弃路由 System，将真实人设 + skill 置于顶端；保留环内已累积的 CMD/工具结果。
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

                try:
                    full_response = await self.llm_client.generate_raw_text(
                        _messages_to_api(final_messages)
                    )
                except CLIENT_GONE_EXCEPTIONS:
                    _handle_client_gone()
                    return

                logger.info(f"[Oligo Core] Full response (final stream): {full_response}")

                chunk_size = 3
                try:
                    for i in range(0, len(full_response), chunk_size):
                        chunk = full_response[i : i + chunk_size]
                        yield _sse_data(chunk)
                        await asyncio.sleep(0.04)
                except CLIENT_GONE_EXCEPTIONS:
                    _handle_client_gone()
                    return

                logger.debug(f"[Oligo Core] Theater concluded on turn {turn}.")
                return

            # ---------- 步骤 D: 耗尽回合，Fallback ----------
            error_msg = (
                "\n\n[SYSTEM FATAL]: Agent exhausted max turns. Shutting down."
            )
            logger.error("[Oligo Core: Fallback] %s", error_msg)
            yield _sse_data(error_msg)

        except CLIENT_GONE_EXCEPTIONS:
            _handle_client_gone()
            return
        except Exception as exc:
            if _looks_like_pipe_broken(exc):
                _handle_client_gone()
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
                if "[SYSTEM TOOL RESULT]" in full_conv:
                    return "Senpai, based on the vault: Titans is flawed. That is all."
                return '<CMD:search_vault({"query": "Titans"})> Searching...'
            if "[SYSTEM TOOL RESULT]" in full_conv:
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
