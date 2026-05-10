# Phase III.B.1 终审 — Prompt Middleware 重构（MW.0–MW.4）

**范围**：`src/oligo/core/prompt_composer.py`、`text_sanitizer.py`、`agent.py`；`docs/PROMPT_MIDDLEWARE.md`；`tests/oligo/`。  
**落盘日期**：与 MW.4 验收同步（审计方：代码审查与自动化检查）。

---

## 红线检查

| 红线 | 结论 | 证据 |
|------|------|------|
| 未破坏 S0.4 CMD 兜底 | 通过 | `_parse_tool_calls` 先经 `TextSanitizer.strip_code_blocks_for_tool_matching`（`agent.py:85-93`, `399`）；围栏/行内例在 `test_tool_execution.py:103-119`、`test_text_sanitizer.py` 仍覆盖 |
| 未删除现有测试 | 通过 | 仅增补/修复 `conftest` 与文档；`test_tool_execution.py` 用例数量未减 |
| 未引入 Oligo 对外新依赖（如 jinja2） | 通过 | 上述模块无 jinja2；Jinja 仅 `src/crucible/ports/prompts/jinja_prompt_manager.py`（既有） |

---

## 维度 1：注入点覆盖完整性

| 子项 | 结论 | 证据与说明 |
|------|------|------------|
| **1.1** MW.0 审计 Q1+Q2+Q3 是否**全部**经 PromptComposer 或 TextSanitizer | **Partial** | **Chimera Router / Final system** 均经 `get_prompt_composer().compose`（`agent.py:345-360`, `371-384`）。**历史与流式**经 `TextSanitizer`（见维度 3）。**未**纳入 PromptComposer：Cognitive Filter 的 **Wash system** 为手写 f-string（`agent.py:685-696`），属独立子系统。若 MW.0 将「Wash」算进 Q2，则 Wash 为**有意不纳入**；若只计 Router/Final，可视为通过。仓库内无 MW.0 审计原文，无法做形式上的逐条对表。 |
| **1.2** 是否仍有直接拼 persona/skill 的 system | **Pass** | `src/oligo` 下无 `f"...{persona}..."` / `+ skill_override +` 拼 Chimera system；`persona`/`skill` 在 `prompt_composer` 模板与 `_prompt_context`（`agent.py:334-343`）。 |
| **1.3** 若有，是否豁免 | **Pass（有意豁免）** | `agent.py:685-696`：Wash 专用 system，**不是** Router/Final 人设；与 `PROMPT_MIDDLEWARE.md` 中默认组件表范围一致。 |

---

## 维度 2：stable / dynamic 分离

| 子项 | 结论 | 证据 |
|------|------|------|
| **2.1** timestamps 等强动态项是否 `cacheable=False` | **Partial** | `dynamic_timestamp`：`cacheable=False`（`prompt_composer.py:191-196`）。**但** `{tool_list}` 在 `router_tool_registry`（`cacheable=True`）中，工具列表变则 stable 字节也变——与「仅时间戳变则 stable 不变」的理想 prefix 模型有张力；`PROMPT_MIDDLEWARE.md` 已说明 `cacheable` 多为**结构意图**；LLM 端是否用 prefix cache 见该文档 **第 6 节 已知未解决问题**。 |
| **2.2** 相同输入下 stable 字节一致 | **Pass** | `tests/oligo/test_prompt_middleware_regression.py`：`test_router_stable_section_byte_identical_on_repeated_compose` |
| **2.3** dynamic 段是否随时间/请求变 | **Pass** | `_prompt_context` 中 `timestamp`（`agent.py:342`）经 `dynamic_timestamp` 进入 dynamic 段 |

---

## 维度 3：三层 Strip 调用纪律

| 子项 | 结论 | 证据 |
|------|------|------|
| **3.1** 位点 A（Final stream chunk）L1 + L2 | **Pass** | `TextSanitizer.strip_tool_syntax_in_visible(TextSanitizer.strip_reasoning_tags(full_response))`（`agent.py:1053-1056`）— 内层 L1、外层 L2 |
| **3.2** 位点 B（probe backfill）L2 | **Pass** | `TextSanitizer.strip_tool_syntax_in_visible`（`agent.py:974-976`） |
| **3.3** 位点 C（每次 LLM 前）L3 | **Pass** | `_apply_history_sanitizer_to_messages`（`agent.py:834`, `1004`）→ `sanitize_messages_history` |
| **3.4** 其他 ad-hoc strip | **Pass（受控）** | `strip_code_blocks_for_cmd_matching` 专用于 **CMD 正则前** S0.4（`agent.py:85-93`, `399`, `945-946`），与 L2 全文「可见区清洗」职责不同。`_is_router_pass_or_trivial` 仅 `.strip()`（`agent.py:116-125`） |

---

## 维度 4：行为等价与回归

| 子项 | 结论 | 证据 |
|------|------|------|
| **4.1** `test_prompt_middleware_regression.py` 全部通过 | **Pass** | 有 `openai` 时 7/7；无则 `importorskip` 跳过需 `ChimeraAgent` 的 2 项 |
| **4.2** `test_tool_execution.py` 全部通过 | **Partial** | `conftest` 中 `mock_client` 已注册为 `@pytest.fixture` 并返回 `MockLLMClient` 类，**同步 9 项通过**。**6 个 `async def`** 在无 `pytest.mark.asyncio` 时仍被 **Skipped**；与 MW 中间件改 agent **无直接因果**；若强行启用 asyncio 会暴露与当前 `run_theater`/mock 不一致的**既有失败**，需单开任务修测 |
| **4.3** S0.4 场景 A/B/C | **Pass** | **A** 围栏：`test_tool_execution.py:102-119`；**B** 非法 JSON：`86-100`；**C** backfill：`test_strip_router_dsl_for_backfill_removes_tags` + `test_text_sanitizer.py` |

**后续建议（非本次 MW 必交）**  
- 为 6 个 async 用例加 `@pytest.mark.asyncio` 并修正 mock/断言与当前 `ChimeraAgent` 行为一致；或于 CI 显式标记 skip/xfail 并建 Issue。

---

## 维度 5：文档与可维护性

| 子项 | 结论 | 证据 |
|------|------|------|
| **5.1** 是否包含「新增 component checklist」 | **Pass** | `PROMPT_MIDDLEWARE.md` 第 4 节（6 步） |
| **5.2** 文档 component 表与 `_register_default_components` | **Pass** | 9 个 id 与 `prompt_composer.py:114-198` 一一对应 |
| **5.3** priority 区段与代码 | **Pass（文档已校）** | `final_guardrail` 使用 priority **10**（非「仅 1–9」）；`docs/PROMPT_MIDDLEWARE.md` 中 priority 表已写为 **1–10** 以覆盖 guardrail=10 与 `dynamic_timestamp=5` |

---

## 封版声明

- **五维度全 Pass：否**（1.1、2.1、4.2 为 **Partial**）。  
- **三条红线：均满足**。  
- **结论**：不以「五维度全 Pass」对 Phase III.B.1 作行政封版；在 **Oligo PromptComposer + TextSanitizer + 已跑通的回归/单测** 范围内，**中间件实现与文档可视为验收通过、红线可盖章**。完整行政封版建议待 **4.2 异步用例** 可执行性落地后再发。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `docs/PROMPT_MIDDLEWARE.md` | 中间件规范与 strip 契约 |
| `docs/PHASE_III_B1_AUDIT_MW_MIDDLEWARE.md` | 本终审落盘件 |
| `tests/oligo/conftest.py` | `mock_client` fixture（`@pytest.fixture`，返回 `MockLLMClient` 类） |
| `tests/oligo/test_prompt_middleware_regression.py` | MW.4 回归基线与 stable 字节断言 |
