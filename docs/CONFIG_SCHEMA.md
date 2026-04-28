# Project Chimera — 统一配置 Schema（Phase 1）

本文档定义 **唯一配置入口** `~/.chimera/config.toml` 的结构、字段类型与约定。实现加载器（后续 Phase）须与此 Schema 对齐。

**物理路径**：由 `crucible.core.platform.get_config_path()`（Python）与 `crate::platform::get_config_path()`（Rust / Astrocyte）解析，即用户主目录下的 `.chimera/config.toml`。

---

## 1. 顶层约定

| 约定 | 说明 |
|------|------|
| 格式 | TOML 1.0 |
| 编码 | UTF-8 |
| 路径类字段 | 允许使用 `~` 与正斜杠；加载时应规范化为绝对路径（`expanduser` + `resolve` 或等价逻辑） |
| 密钥类字段 | Schema 允许出现在文件中；生产环境建议由环境变量覆盖（实现阶段定义 `CHIMERA_*` 映射） |
| 未出现的表/键 | 实现可使用文档中的 **默认值**；若默认值为「无」，则表示可选 |

---

## 2. 配置块总览

| 表名 | 职责 |
|------|------|
| `[system]` | 全局：Vault 根路径、技能目录、日志级别 |
| `[oligo]` | Oligo 服务：监听与 Agent 运行参数 |
| `[llm]` | 默认 LLM 行为；具体供应商在 `[llm.providers.*]` |
| `[llm.providers.<name>]` | 具名供应商：`api_key`、`base_url` 等 |
| `[wash]` | Oligo Wash（压缩 / 廉价模型路径）阈值与工具策略 |
| `[vault]` | Vault 读缓存与 TTL |
| `[astrocyte]` | 桌面客户端 UI 与行为开关 |

---

## 3. `[system]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `vault_root` | string (path) | 是* | — | Obsidian / Exocortex Vault 根目录；必须为可解析路径 |
| `skills_dir` | string (path) | 否 | `{chimera_root}/skills` | 技能 JSON 目录；与平台抽象层默认一致时可省略 |
| `log_level` | string (enum) | 否 | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

\*Phase 1 Schema 将 `vault_root` 标为业务强依赖；若实现允许「仅 Astrocyte 无 Vault」，可在实现中放宽并在此文档修订。

---

## 4. `[oligo]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `host` | string | 否 | `127.0.0.1` | HTTP 监听地址 |
| `port` | integer | 否 | `33333` | HTTP 监听端口（1–65535） |
| `max_turns` | integer | 否 | `10` | 单次会话 Agent 最大轮次（≥1） |
| `tool_execution_deadline_seconds` | integer | 否 | `45` | 单次工具执行超时（秒，≥1） |

---

## 5. `[llm]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `default_timeout_seconds` | float 或 integer | 否 | `90` | 默认 HTTP / 客户端等待上限（秒） |
| `default_model` | string | 否 | `deepseek-chat` | 未指定模型时的逻辑名 |
| `default_temperature` | float | 否 | `0.7` | 采样温度，典型范围 0.0–2.0 |

### 5.1 `[llm.providers.<provider_id>]`

`<provider_id>` 为 TOML 表键，小写标识符，例如 `openai`、`deepseek`。

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `api_key` | string | 否* | — | API 密钥；可由环境变量覆盖 |
| `base_url` | string (URL) | 否* | — | OpenAI 兼容 API 根 URL |

\*至少在使用该 provider 发起请求前必须能通过配置或环境解析出有效值。

**示例表名**：`[llm.providers.openai]`、`[llm.providers.deepseek]`。可扩展更多表（如 `anthropic`、`gemini`），Schema 结构相同。

---

## 6. `[wash]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `min_chars` | integer | 否 | `1200` | 触发 Wash 的上下文长度阈值（字符或实现约定单位） |
| `bypass_tools` | array of string | 否 | `["search_vault_attribute", "metadata_lookup"]` | 不经过 Wash 的工具 id 列表 |
| `force_tools` | array of string | 否 | `["search_vault", "web_search"]` | 强制走 Wash 或高优先级压缩路径的工具列表（语义以实现为准） |

---

## 7. `[vault]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `cache_size` | integer | 否 | `200` | Vault 读缓存最大条目数（≥0） |
| `cache_ttl_seconds` | integer | 否 | `300` | 缓存 TTL（秒，≥0） |

---

## 8. `[astrocyte]`

| 键 | 类型 | 必填 | 默认值 | 说明 |
|----|------|------|--------|------|
| `theme` | string (enum) | 否 | `dark` | `dark` \| `light` |
| `enable_clipboard_capture` | boolean | 否 | `false` | 是否启用剪贴板捕获相关能力 |

---

## 9. 与旧配置的映射（实现阶段，非本文档范围）

| 旧来源 | 新位置（目标） |
|--------|----------------|
| `crucible_core/config.yaml` 中 `vault_root`、`oligo.*`、`default_llm_*` 等 | 对应迁入 `[system]`、`[oligo]`、`[llm]` |
| 环境变量 / `.env` 中的密钥 | 仍可作为覆盖层；键名在实现 Phase 定义 |
| Astrocyte `provider_config.json` | 逐步收敛到 `[llm.providers.*]` 与 `[astrocyte]` |

---

## 10. 完整结构示例（无注释，校验用）

```toml
[system]
vault_root = "~/Documents/Obsidian/MyVault"
skills_dir = "~/.chimera/skills"
log_level = "INFO"

[oligo]
host = "127.0.0.1"
port = 33333
max_turns = 10
tool_execution_deadline_seconds = 45

[llm]
default_timeout_seconds = 90
default_model = "deepseek-chat"
default_temperature = 0.7

[llm.providers.openai]
api_key = "sk-..."
base_url = "https://api.openai.com/v1"

[llm.providers.deepseek]
api_key = "sk-..."
base_url = "https://api.deepseek.com"

[wash]
min_chars = 1200
bypass_tools = ["search_vault_attribute", "metadata_lookup"]
force_tools = ["search_vault", "web_search"]

[vault]
cache_size = 200
cache_ttl_seconds = 300

[astrocyte]
theme = "dark"
enable_clipboard_capture = false
```

---

## 11. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| Phase 1 | 2026-04-24 | 首次 Schema 制定；仅文档与模板，无加载器实现要求 |
