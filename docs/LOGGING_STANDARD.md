# Project Chimera 日志规范

## 格式标准

所有 Python 脚本必须使用以下格式：

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

## 前缀规范

日志消息必须使用以下前缀：

- `[Oligo]` — Oligo Agent 核心逻辑（含 FastAPI 网关与 SSE 流生命周期）
- `[Router]` — 路由阶段
- `[Tool]` — 工具执行
- `[Wash]` — 清洗阶段
- `[Final]` — 最终推流（剧场终局缓冲与分片下发）
- `[Vault]` — Vault 检索与相关适配器
- `[LLM]` — LLM 客户端（OpenAI 兼容层、重试与流超时）
- `[Config]` — `config.py` / `platform.py`：统一配置与数据目录迁移
- `[Bootstrap]` — `bootstrap.py`：客户端装配与旧版 Wash 环境提示
- `[Notify]` — `ports/notify/*`：Telegram 等通知
- `[Ingest]` — `ports/ingest/*`：MinerU / PDF 摄入管线
- `[Arxiv]` — `ports/arxiv/*`：arXiv API 与 PDF 下载
- `[Paper]` — `ports/papers/*`：论文加载、归档与审计日志
- `[Prompt]` — `ports/prompts/*`：Jinja 模板加载与渲染
- `[Service]` — `services/*`：批处理、管线、Optics、筛选等业务服务
- `[Astrocyte]` — Rust 端日志（通过 `log` crate / `eprintln!`）

## 禁止的模式

- 不同脚本使用不同的 `format` 字段顺序
- 混用 `[Oligo Core]` 和 `[Oligo]`
- 混用 `[Stream]` 与 `[Oligo]` / `[Final]`（SSE 网关层统一用 `[Oligo]`；终局推流逻辑用 `[Final]`）
- 在消息中重复 levelname（如 `logger.info("[INFO] ...")`）
