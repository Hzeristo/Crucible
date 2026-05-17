# 意图识别与降级（IR）：设计意图与已知边界

本文档说明 **Router 工具说明（ToolSpec）富化**、**工具失败后的 LLM 侧展示与 reflection hint** 的设计动机，以及当前 **不会自动“重试同一调用”** 的行为边界。实现主路径：`src/oligo/core/prompt_composer.py`、`src/oligo/core/agent.py`、`src/crucible/core/schemas.py`。

---

## 1. Tool schema 富化的设计意图

**目标**：让 Router 在同一份 system 里看到**结构化参数说明**（类型、是否必填、短 `help`），降低胡编字段名与漏填必填项的概率；同时在超长时**可降级**为紧凑列表，避免撑爆路由上下文。

**模型与契约**

- 每个注册工具通过 `ToolSpec.args_schema` 描述参数元数据（JSON-schema-like 字典）：

```575:583:src/crucible/core/schemas.py
class ToolSpec(BaseModel):
    """工具的元数据"""

    name: str = Field(description="工具名, 与 TOOL_REGISTRY 的 key 一致")
    description: str = Field(description="一行简介, 用于 router prompt")
    args_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-schema-like 描述, 形如 {'query': {'type': 'str', 'required': True}}",
    )
```

- Router 工具块由 `_format_one_tool_verbose` 逐工具渲染：无 schema 时明确写「仅有可选键则用 `{}`」；有 schema 时逐参数列出 `type` / `required` / `help`，并附带 XML 与 CMD 示例（便于对齐 `TOOL_PROTOCOL`）：

```101:126:src/oligo/core/prompt_composer.py
def _format_one_tool_verbose(spec: ToolSpec) -> str:
    schema = spec.args_schema or {}
    title = spec.name
    if spec.long_running:
        title += " [long_running → returns task_id; poll with the status tool from the same list]"
    lines = [f"### {title}", spec.description.strip()]
    if not schema:
        lines.append("Parameters: (none declared; use {{}} if the tool accepts only optional keys)")
    else:
        lines.append("Parameters:")
        for pname, raw in schema.items():
            meta = _param_meta(raw)
            typ = str(meta.get("type", "str"))
            req_lbl = "required" if bool(meta.get("required")) else "optional"
            help_t = str(meta.get("help", "")).strip()
            if help_t:
                lines.append(f"  - {pname} ({typ}, {req_lbl}): {help_t}")
            else:
                lines.append(f"  - {pname} ({typ}, {req_lbl})")
    ex = _example_args_for_spec(spec)
    payload = json.dumps(ex, ensure_ascii=False)
    lines.append(
        f"Example (XML): <tool_call name=\"{spec.name}\"><args>{payload}</args></tool_call>"
    )
    lines.append(f"Example (CMD): <CMD:{spec.name}({payload})>")
    return "\n".join(lines)
```

- 总长度受 `_render_tool_list` 与 Router system 组装时的 **多档 `max_chars` 预算**约束（见 `ChimeraAgent._build_router_system_prompt` 与 `prompt_composer._render_tool_list`），超长时依次降级为 compact / micro / 硬截断。

**注意**：富化的是 **Router 肉眼可读说明**；真正执行时参数仍来自模型输出的 `<tool_call>` / `<CMD:…>`，经 `tool_protocol` 解析与校验（见 `docs/TOOL_PROTOCOL.md`）。

---

## 2. Failure reflection hint 的触发规则

工具结果注入给模型的正文由 `_render_tool_results_for_llm` 组装：每条结果为 `<tool_result …>` 包装，并在文末**条件性**追加简短英文 hint（非第二趟独立 LLM，仅为静态文案）。

**（1）渲染层失败原因枚举**（与 `ToolCallStatus` 及正文启发式组合，**仅用于** `<tool_result reason="…">`，不写入 `ExecutedToolResult`）：

```127:131:src/oligo/core/agent.py
_TR_REASON_DENIED = "DENIED"
_TR_REASON_TIMEOUT = "TIMEOUT"
_TR_REASON_TOOL_ERROR = "TOOL_ERROR"
_TR_REASON_ARGS_INVALID = "ARGS_INVALID"
_TR_REASON_EMPTY_RESULT = "EMPTY_RESULT"
```

分类逻辑见 `_classify_render_outcome`（如 `DENIED` / `TIMEOUT` / `ERROR` 与正文是否像参数错误、是否空结果等）：`215:252:src/oligo/core/agent.py`。

**（2）何时追加 `_REFLECTION_HINT_FAILURE`**

- 若本轮渲染中**任意**一条结果显示为 `status="failed"`（即 `any_failed_display`），则追加「Some tools failed. Consider: (a) retry… (b) alternative tool… (c) tell the user…」。

**（3）何时追加 `_REFLECTION_HINT_EMPTY`**

- 若存在 `reason` 为 `EMPTY_RESULT` 的失败项，**且** 当前会话**最近一条 user 正文**（`_latest_user_message_text`）经 `_user_text_suggests_expectation` 判断为“像在要检索/查找结论”的语气，则再追加「Empty result… broadening… web_search…」。

实现片段：

```1033:1075:src/oligo/core/agent.py
    def _latest_user_message_text(self) -> str:
        """最近一次 user 消息正文，用于 IR.2 空结果 reflection hint。"""
        for m in reversed(self.raw_messages):
            if m.role == "user":
                return (m.content or "").strip()
        return ""

    def _render_tool_results_for_llm(
        self,
        results: list[ExecutedToolResult],
    ) -> str:
        """Format executed tool rows into one stable user message (no LLM calls)."""
        parts: list[str] = ["[SYSTEM TOOL RESULTS]", ""]
        any_failed_display = False
        any_empty_result = False
        user_expects = _user_text_suggests_expectation(
            self._latest_user_message_text()
        )
        ...
        hint_lines: list[str] = []
        if any_failed_display:
            hint_lines.append(_REFLECTION_HINT_FAILURE)
        if any_empty_result and user_expects:
            hint_lines.append(_REFLECTION_HINT_EMPTY)
        hint_lines = hint_lines[:3]
```

关键词表：`_USER_EXPECTATION_KEYWORDS`（`141:155:src/oligo/core/agent.py`）；空结果启发式：`_body_looks_empty_for_hint` / `_EMPTY_SUCCESS_MARKERS`（`157:212:src/oligo/core/agent.py`）。

---

## 3. 已知未解决问题：Router 不会“自动 retry”

- **现象**：当工具失败或结果为空时，hint 会建议 **换参数重试、换工具、或向用户坦白**；**但不会**由运行时自动再发起一次相同的 `PlannedToolCall`。
- **实际行为**：下一轮到 **Router 探针**（`_run_theater_stream` 中的 `generate_raw_text`）时，由模型**重新阅读**带 `[SYSTEM TOOL RESULTS]` 与 `reason="…"` 的历史，自行决定是再调工具、换工具，还是走 `<PASS>` 进入 Final。也可能在 `max_turns` 内反复尝试不同策略，或最终放弃工具走纯 Final。
- **影响**：若模型忽略 `reason` 或未稳定遵循 hint，仍可能出现“错误重复”或过早 `<PASS>`；缓解依赖 prompt、工具列表质量与后续产品化策略（不在 IR.5 范围内改调度器自动重试）。

---

## 4. 相关工程产物

- **工具开始/结束 SSE**（前端读秒与遥测）：见 `docs/TOOL_PROTOCOL.md` §5。仅在**至少有一条** `allowed=True` 的计划进入 `_execute_tool_plan_batch` 时才会发出 `bb-tool-start` / `bb-tool-done`；解析阶段即 `allowed=False`（未注册工具名、`tool_name` 非 `[a-zA-Z0-9_]+`、args JSON 无法修复等）时 `_execute_tool_calls` 直接返回、**无**工具 SSE（`866:874:src/oligo/core/agent.py`）。载荷与批调度：`732:742:src/oligo/core/agent.py`、`794:807:src/oligo/core/agent.py`、`839:851:src/oligo/core/agent.py`、`856:864:src/oligo/core/agent.py`；帧格式 `8:10:src/oligo/core/sse.py`；Astrocyte 转发 `307:323:../../astrocyte/src-tauri/src/llm_client.rs`（路径相对 `crucible_core/docs/`）。
- **冒烟（封版 Agent 栈）**：`scripts/smoke_intent_recognition.py` — 多轮 ReAct、**真实** `read_vault_file` / `search_vault` 执行、SSE `bb-tool-*`；第二轮 Router 仅在上下文中出现 `Some tools failed.` 时才返回 `search_vault`，用于验证 **hint 条件化换工具**（非 pytest）。
- **可选真模型**：`scripts/smoke_intent_router_live.py` — 使用 `settings.llm.working`；若 API 未配置则 exit 2（跳过）；若配置齐全，exit 0 表示流中同时出现两种工具名（模型若未照做则为 exit 1）。

---

## 修订与同步

- 修改 `_render_tool_results_for_llm`、`_classify_render_outcome` 或 `ToolSpec` / `_format_one_tool_verbose` 时，同步更新本文档与 `docs/TOOL_PROTOCOL.md`。
