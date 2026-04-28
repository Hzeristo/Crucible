# Oligo Tool Protocol（工具协议）

本文档描述 Chimera / Oligo 侧**工具调用协议**的演进、双格式语法、参数修复、并发分批模型及扩展流程。实现主路径：`src/oligo/core/tool_protocol.py`、`src/oligo/core/agent.py`、`src/oligo/tools/registry.py`。

---

## 1. 协议演进

### V1：`<CMD:tool_name({...})>`（遗留）

- **特点**：单行友好、与早期 prompt 深度绑定。
- **局限**：括号与 JSON 边界依赖正则 `(.*?)`；复杂引号、多行、嵌套示例易与「真调用」混淆（部分场景由 **剥离 Markdown 代码块后再匹配** 缓解）。

### V2：`<tool_call>` XML

- **特点**：结构化的开闭标签；`<args>` 内承载 JSON 文本，利于多行与元数据扩展；解析以 `xml.etree` 为主、正则为兜底。
- **可选属性**：`name`（必需）、`id`、`timeout` 等（见 [§2](#2-xml-语法规范)）。

### 双轨共存策略

- **同一轮路由输出**可同时出现 V1 与 V2；统一入口 `parse_tool_calls_unified()` 在**已去掉围栏/行内代码的文本**上匹配，按**文档出现顺序**合并结果（先匹配的格式先入列）。
- **长期方向**：以 XML 为主、CMD 为兼容层（见路由 System 中 Format A / B 说明）。

---

## 2. XML 语法规范

### 标准形式

```xml
<tool_call name="search_vault">
  <args>{"query": "memory architecture"}</args>
</tool_call>
```

- 开标签：`tool_call` 与属性之间须有空白；`name` 为注册表中的工具名。
- 子元素 **`args`**：内容为**单一 JSON 对象**的文本（与 CMD 括号内要求一致）；缺省或空文本按 `{}` 处理（见实现）。

### 可选属性

| 属性 | 说明 |
|------|------|
| **`id`** | 若存在，写入 `PlannedToolCall.id`，供结果与日志关联；为后续 multi-tool / 客户端配对预留。 |
| **`timeout`** | **仅解析接受**，不改变每工具调度层 `wait_for` 死线（当前不实现 per-call 超时覆盖）。 |

### 多 `tool_call` 并存

- 同一响应可出现多个完整 `<tool_call ...>...</tool_call>` 块；与多个 `<CMD:...>` 一样，由 agent **分批执行**（见 [§4](#4-concurrency-safety-模型)）。
- 在 **Markdown 围栏或行内反引号** 内讨论示例时，运行时**不会**对剥离后的文本执行匹配（与 CMD 的 S0.4 策略一致）。

---

## 3. Argument Repair 策略清单

当 `json.loads(raw_args)` 失败时，`attempt_argument_repair()` 按**固定顺序**尝试格式修复；**仅当修复后 `loads` 成功且根值为 `object` 或 `string`** 才接受（`string` 再按历史规则宽松为 `{"query": ...}`）。  
任何生效的修复步骤记入 `repairs_applied`（`PlannedToolCall`），并打 **`[Tool] Args repaired for ...` INFO 日志**；SSE 可在 router 阶段带 `args_repaired` / `repairs_applied`。

**优先级（实现顺序）**

1. **`strip_code_fence`**：去掉外层 ` ``` ` / ` ```json ` 包裹。
2. **`single_to_double_quote`**：若存在 `'` 且不存在 `"`，将 `'` 换为 `"`（Python 风格 → JSON）。
3. **`trailing_comma`**：去掉 `,` 紧邻 `}` / `]` 的尾随逗号（正则一轮替换）。
4. **`wrap_braces`**：若不以 `{` / `[` 开头且含 `:`，在外层补 `{` `}`（仅格式包裹，不改写键值内容）。
5. **`smart_quotes`**：Unicode 弯引号 U+201C / U+201D → 直引号 `"`。

**修复失败**

- 若仍无法解析或根类型非 `dict`/`str`：抛出 `ValueError`，agent 侧与历史一致 → **`allowed=False` / DENIED**（不静默执行）。

**依赖**：标准库 `json` / `re` 等；**不**使用 json5、demjson 等。

---

## 4. Concurrency Safety 模型

### `concurrency_safe=True`（判据）

- **只读或无副作用冲突**：并发执行多个调用时，不依赖彼此顺序、不与其他 safe 调用争用需互斥的本地资源（如「同一 vault 只读检索」）。
- 调度：连续多个 safe 调用可进入**同一批**，批内 `asyncio.gather` 并发。

### `concurrency_safe=False`（判据）

- **写文件、改进程内任务状态、长流水线**：例如启动 arXiv 拉取、日报 pipeline 等；或与「独占资源」强相关、不宜与别的工具并行。
- 调度：**独占一批**；批大小为 1 → 串行 `await`（与 gather 单任务等价，语义清晰）。

### 现行工具分类（`ToolRegistry` / `_register_default_tools`）

| 工具 | `concurrency_safe` | `long_running` | 备注 |
|------|--------------------|----------------|------|
| `search_vault` | 是 | 否 | 只读检索 |
| `search_vault_attribute` | 是 | 否 | 只读检索 |
| `obsidian_graph_query` | 是 | 否 | 只读图查询 |
| `web_search` | 是 | 否 | 外网只读 |
| `check_task_status` | 是 | 否 | 只读轮询 |
| `arxiv_miner` | 否 | 是 | 写状态 / 异步任务 |
| `daily_paper_pipeline` | 否 | 是 | 多阶段长任务 |

未注册工具名：`is_concurrency_safe` 视为 **False**（偏保守，独占批）。

---

## 5. 新增工具的 checklist

1. **填写 `ToolSpec`**：`name`、`description`（router 一行摘要）、`args_schema`、`concurrency_safe`、`long_running`、`examples`（可选）。
2. 在 **`registry.py` 的 `_register_default_tools`** 中 `reg.register(func, spec)`；保持 `TOOL_REGISTRY` 由 `get_tool_registry()` 派生（见 `tools/__init__.py`）。
3. **单测**：解析/白名单/修复/分批中与该工具相关的路径各至少一条；若有 vault / 网络依赖，用 mock 或现有 fixture。
4. **更新本文档 [§4 表格](#4-concurrency-safety-模型)**，避免文档与注册表漂移。

---

## 6. 已知未解决问题

1. **`tool_call` 的 `id` / `timeout`**：当前为**接受但不强制**；`timeout` **不**覆盖全局 `tool_execution_deadline_seconds`。
2. **无 DAG 依赖**：工具之间**不**声明「B 必须在 A 之后」；多调用顺序仅由**模型输出顺序**与 **safe/unsafe 分批**决定。引入 DAG 属 **有意不在 Phase III.B** 范围内。

---

## 修订与同步

- 修改解析、修复、分批或 `ToolSpec` 默认注册时：同步更新本文件与 `docs/PROMPT_MIDDLEWARE.md`（若影响 router 文案/工具列表行为），并维护 `tests/oligo/` 下相关回归。
