# Oligo SSE 协议规范

Oligo 与下游（如 Astrocyte / Rust `eventsource` 客户端）之间的流式协议采用 **带命名事件（Named Event）** 的 SSE 帧。工具遥测、路由探针等仍可使用无 `event:` 的 ``data: {"content": "..."}`` 默认消息（见下文「与旧式负载的区别」），但**流结束**与**正文分片**必须使用下述命名事件，不得再内嵌在 `content` 字符串中。

## 事件类型

### 1. `bb-stream-chunk`

- **用途**：正常回答片段（晚期绑定后的全量推流，按片下发）。
- **data JSON**：`{ "content": string }`。
- **帧示例**：
  ```http
  event: bb-stream-chunk
  data: {"content": "…"}

  ```

### 2. `bb-stream-done`

- **用途**：流结束或异常收束的**唯一**信号；`data` 为 JSON 对象，客户端以 `msg.event == "bb-stream-done"` 识别（与内嵌在 `content` 中的伪 URI 划清界限）。

- **可能负载**（互斥或组合，以具体场景为准）：

| 场景 | 示例 `data` |
|------|----------------|
| 用户/客户端断连、管道破裂（agent 内捕获） | `{"aborted": true, "reason": "client_gone"}` |
| `safe_theater_stream` 外层的客户端断开 | 同上 |
| 未预期错误（API 层捕获） | `{"error": true, "message": "…"}`（正文可能截断） |
| LLM 网关/路由/终局缓冲超时 | `{"error": true, "message": "LLM gateway timeout"}` |
| 达到 `max_turns` | `{"error": true, "message": "<SYSTEM FATAL 文案>"}` |
| 正常完成（**可选**；若未发送，可由连接关闭表示结束） | `{}` 或与产品约定一致的空对象 |

- **与 API 层对齐**：`message` 字段在错误类负载中与 `error: true` 成对使用，便于 Rust/前端与聊天正文分流展示。

## 废弃格式

以下格式**不再使用**：

```http
data: {"content": "bb-stream-done: …"}

```

即：不得在 `data` 的 `content` 字段中拼接以 `bb-stream-done:` 为前缀的伪协议字符串。结束状态一律使用 **`event: bb-stream-done` + JSON `data`**。

## 实现说明

- 标准帧由 `src/oligo/core/sse.py` 中的 `sse_event(event_type, data)` 生成，避免手写换行与转义错误。
- Agent 中 `_sse_data()` 仍产生默认（无 `event`）的 `data: {"content": "…"}`，用于 `__SYS_TOOL_CALL__` 等遥测与路由阶段负载；**与 `bb-stream-done` / `bb-stream-chunk` 正交**。
