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
        ValueError: 解析结果不是 dict。
    """
    data = json.loads(raw_args.strip())
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


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
    剧场版 ReAct Agent：前期阻塞探包 + 后期全量推流。

    工作原理简述：
    1. 前期阻塞探包：在工具调用阶段，使用非流式 generate_raw_text 获取完整回答，
       避免流式输出在 <CMD> 标签处截断导致状态机错乱。
    2. 后期全量推流：当检测到无 <CMD> 时，将完整回答以小批量 SSE 帧流式输出，
       提供打字机体验且不破坏前端解析。
    3. 内部消息流严格为 list[ChatMessage]，仅在调用 llm_client 时通过 model_dump
       转化为网络层接受的 dict 列表。
    """

    def __init__(
        self,
        messages: list[dict[str, Any]] | list[ChatMessage],
        llm_client: Any,
        max_turns: int = 5,
    ) -> None:
        """
        初始化剧场版 Agent。

        Args:
            messages: 对话历史。支持 list[dict]（将自动强转为 ChatMessage）
                或 list[ChatMessage]。不允许混入不可控字段。
            llm_client: 大模型客户端，需实现 generate_raw_text(messages) 异步方法。
                接收 list[dict] 作为参数，本类在调用时会自动完成 model_dump 映射。
            max_turns: 最大 ReAct 轮次，超出后进入 Fallback 并停止。
        """
        self.messages: list[ChatMessage] = _ensure_chat_messages(messages)
        self.llm_client = llm_client
        self.max_turns = max_turns

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
            return f"Error: Tool '{tool_name}' is not recognized by the Chimera OS."
        fn = TOOL_REGISTRY[tool_name]
        try:
            args_dict = _parse_tool_args(raw_args)
        except json.JSONDecodeError as e:
            return (
                f"Error: Invalid tool args. Must be JSON object e.g. {{\"query\": \"...\"}}. "
                f"You sent: {raw_args!r}. Parse error: {e}"
            )
        except ValueError as e:
            return f"Error: {e}"
        try:
            result = await fn(**args_dict)
        except TypeError as e:
            return f"Error: Tool '{tool_name}' invalid args: {e}"
        return str(result)

    async def run_theater(self) -> AsyncGenerator[str, None]:
        """
        剧场版主循环：闭门思考 → 检查 CMD → 终极推流。

        步骤说明：
        A) 闭门思考：非流式 generate_raw_text，拿到完整回答。
        B) 检查 <CMD>：若有则执行工具、注入结果、continue。
        C) 无 <CMD>：终极推流（小批量 SSE 模拟流式），break。
        D) turn > max_turns：进入 [Oligo Core: Fallback]，yield 错误信息并结束。

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
                        "==> [Oligo Core] DUMPING SYS PROMPT (first 150 chars): %s",
                        preview,
                    )
                api_messages = _messages_to_api(self.messages)
                try:
                    full_response = await self.llm_client.generate_raw_text(
                        api_messages
                    )
                except CLIENT_GONE_EXCEPTIONS:
                    _handle_client_gone()
                    return

                # ---------- 步骤 B: 检查结果 ----------
                logger.info(f"[Oligo Core] Full response: {full_response}")
                match = CMD_REGEX.search(full_response)

                if match:
                    tool_name = match.group(1)
                    tool_args = match.group(2)

                    # 向下游发送系统工具调用信标，供 Rust/前端进行专用事件分流
                    try:
                        yield _sse_data(f"__SYS_TOOL_CALL__{tool_name}::{tool_args}")
                    except CLIENT_GONE_EXCEPTIONS:
                        _handle_client_gone()
                        return

                    # 执行工具
                    try:
                        tool_result = await self._execute_tool(tool_name, tool_args)
                    except CLIENT_GONE_EXCEPTIONS:
                        _handle_client_gone()
                        return

                    # 只将 <CMD:...> 片段放入 messages，不含前后其他文字
                    self.messages.append(
                        ChatMessage(role="assistant", content=match.group(0))
                    )

                    # 将工具结果注入（user 伪装）
                    self.messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                f"[SYSTEM TOOL RESULT]:\n{tool_result}\n\n"
                                "Maintain your persona and continue. DO NOT output <CMD> again."
                            ),
                        )
                    )

                    continue

                # ---------- 步骤 C: 终极推流 ----------
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
            if "Mock Tool Result" in full_conv or "[SYSTEM TOOL RESULT]" in full_conv:
                return "Senpai, based on the vault: Titans is flawed. That is all."
            return '<CMD:search_vault({"query": "Titans"})> Searching...'

        async def stream_generate(
            self, messages: list[dict]
        ) -> AsyncGenerator[str, None]:
            for c in "Senpai, Titans is flawed. That is all.":
                yield c
                await asyncio.sleep(0.03)

    async def test_run():
        agent = ChimeraAgent(
            messages=[{"role": "user", "content": "Fetch Titans."}],
            llm_client=MockLLMClient(),
        )
        print("Frontend receives:", end="", flush=True)
        async for chunk in agent.run_theater():
            print(chunk, end="", flush=True)
        print("\n\nDone.")

    asyncio.run(test_run())
