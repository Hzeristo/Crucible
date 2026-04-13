# 《架构溃烂法医诊断书》(The Architecture Rot Diagnostics)

**审查范围**：`crucible_core/src/`、`crucible_core/prompts/`、`crucible_core/scripts/`（并交叉引用 `config` / `llm_gateway`）。  
**方法论**：目录与 import 关系、关键路径上的重复策略、LLM 与模板加载链路的静态追踪。  
**说明**：本文件为只读审计结论的归档，不包含代码修改或自动化修复脚本。

---

## 审查维度 1：物理域与架构碎裂 (The Topography Breakdown)

### 1.1 「平起平坐的僭越」与 DDD 语境下的精神分裂

**现状骨架（摘要）**

| 区域 | 组织方式 | 典型路径 |
|------|----------|----------|
| **PaperMiner** | 套娃式「迷你单体」：`core/`、`decision/`、`io_adapter/`、`workflows/` 自成闭环 | `src/miners/paperminer/` |
| **Oligo** | 服务化分层：`api/`、`core/`、`domain/`、`tools/` | `src/oligo/` |
| **Optics** | 近乎裸露的扁平包：若干 `.py` 顶在 `src/optics/` 根上 | `src/optics/engine.py`、`loader.py`、`vault_indexer.py` 等 |

**罪证与逻辑**

- **PaperMiner** 在 `miners/` 命名空间下却承载了「领域模型 + 决策 + IO + 编排」全栈，与「矿工」语义不符；内部子包名与顶层 **`src/crucible/io_adapter/`**（如 Telegram）再次撞车——两套都叫 `io_adapter`，职责却毫无统一边界，属于**命名空间层面的山寨分封**。
- **Optics** 没有与 PaperMiner 对称的分层，却把 **Vault 寻址**（`vault_indexer.py`）、**透镜加载与巨型内嵌指令**（`loader.py`）、**引擎**（`engine.py`）硬塞在同一扁平层；与 PaperMiner 的「工作流门面」(`workflows/chimera_daily.py`、`batch_filter.py`) 形成两套并行宇宙。
- **Oligo** 看起来像独立产品（FastAPI、`ChimeraAgent`），却与同一 `crucible.llm_gateway` 共享；三者之间没有清晰的 **Bounded Context** 边界文档或模块级契约，仅靠路径约定和 `Settings` 字段耦合。

**分级**

| 级别 | 结论 |
|------|------|
| **Fatal** | 同一仓库内三套「垂直切片」深度不一（迷你单体 / 服务分层 / 扁平脚本包），**无法在同一套 DDD 图里画出一个一致的限界上下文**；新人只能凭肌肉记忆猜该改哪棵树。 |
| **Critical** | `miners/paperminer` 的体量与内部分层表明它实质是**嵌入在 `miners` 下的应用**，却与 `optics`、`oligo` 在目录隐喻上「平级」，造成所有权与演进策略的长期冲突。 |
| **Major** | `src/miners/__init__.py` 等包装层对「miner」语义的粉饰，与真实职责（全文管线 + Vault + LLM）严重错位。 |

---

### 1.2 `scripts/`：薄入口 vs 第二编排层

**相对「薄」的脚本**（约几十行）：`run_batch_filter.py`、`run_ingest.py`、`run_daily.py`、`start_oligo.py` —— 多数只做 `sys.path` 注入、解析参数、调一层库函数。

**明显发胖的「上帝脚本」**

| 文件 | 问题 |
|------|------|
| **`scripts/run_lens.py`** | 完整串联：**Vault 认证**（`find_paper_in_vault`）、**在 `filtered` 树里二次猜全文 MD**（`_resolve_filtered_fulltext_markdown`，含 `rglob`、判决目录优先级、按文件大小排序）、**PaperLoader**、**OpticsEngine**、**VaultWriter + PromptManager**、异步与退出码。这是典型的 **Composition Root 掉进 CLI**，与「薄入口」背道而驰。 |
| **`scripts/run_single.py`** | 单文件跑通 **ingest → load → filter → vault → router → finally 清理**，阶段日志与异常分支堆满脚本层，生命周期与业务编排纠缠。 |

**分级**

| 级别 | 结论 |
|------|------|
| **Fatal** | `run_lens.py` 把**寻址策略、管线编排、资源构造**捆在一处；任何路径规则变更需要同时懂 Vault、`papers/filtered` 与 Optics，**变更面呈扇形爆炸**。 |
| **Critical** | `run_single.py` 承担「编排 + 清理策略」，与 `workflows/` 内逻辑重复风险极高，属于**脚本层第二业务层**。 |
| **Major** | 多个脚本重复 `_project_root()` + `sys.path.insert` 样板，说明**包安装/入口形态从未被当成一等公民**（PEP 517/可编辑安装缺席的代偿症状）。 |

---

## 审查维度 2：控制反转不彻底与职责重叠 (The Coupling Spaghettification)

### 2.1 IO 的无政府状态与逻辑克隆

**Pathlib 散弹统计（代表性，非穷举）**

- **递归扫 Markdown**：`paper_loader.py`（`rglob("*.md")`）、`vault_indexer.py`（`vault_root.rglob("*.md")`）、`run_lens.py`（`filtered_dir.rglob("*.md")`）、`obsidian_search.py`（`vault.rglob("*.md")`）。
- **读文本**：`paper_loader.py`、`loader.py`（透镜侧读 MD）、`obsidian_search.py` 等各自 `read_text`。

虽未出现 `os.path.join`（仓库已全面 pathlib 化），但**「谁在什么根目录下 rglob、按什么规则认 arXiv id」**并未收敛到单一 **FileLocator / ArtifactRepository** 抽象。

**克隆与契约撕裂**

1. **命名契约**
   - `crucible/utils/filename.py` 的 `compute_fancy_basename` 定义 `{paper.id}-{short_moniker}`。
   - `vault_indexer._extract_short_moniker` 手工解析 `{arxiv_id}-*` 文件名。
   - `run_lens._resolve_filtered_fulltext_markdown` 再次拼装 `expected_stem = f"{arxiv_id}-{short_moniker}"` 并在 `filtered` 下 `rglob`。
   三者**共享同一字符串格式**，却分三处维护；任一规则漂移即产生「Vault 里有、filtered 里找不到」的灵异现场。

2. **Vault 资产路径**
   - `file_router.PaperRouter._promote_pdf_to_vault`：在 `vault_assets_dir` 缺失时退回 `vault_root / "02_Assets" / "Papers"`（与 `Settings` 注释一致）。
   - `vault_indexer.find_paper_in_vault`：对 `settings.require_path("vault_assets_dir")` 做 PDF `rglob`。
   若配置与隐式默认不一致，**索引与路由可能指向不同物理目录**，属于静默数据不一致。

**分级**

| 级别 | 结论 |
|------|------|
| **Fatal** | **全文 MD 解析策略**同时存在于 `PaperRouter`（归档侧）与 `run_lens`（CLI 侧）的认知里；`run_lens` 内 `_FILTERED_VERDICT_DIRS` 注释直接承认与 `file_router.route_and_cleanup` **目录约定硬同步**——这是分布式系统里最恶心的「注释级契约」。 |
| **Critical** | `vault_indexer` 与 `run_lens` 的「按 arXiv id 在文件名里出现即匹配」策略偏启发式，**多笔记同 id 或命名污染时行为未定义**，却在 Optics 主路径上被当作认证真理。 |
| **Major** | `oligo/tools/obsidian_search.py` 再实现一套 Vault 遍历，与 `vault_indexer` **功能重叠**，属于典型的「工具箱里三把都是锤子」。 |

---

### 2.2 LLM Client：公共基建还是业务后门？

**`OpenAICompatibleClient` 实例化热点**

- `scripts/run_lens.py`、`scripts/run_single.py`
- `miners/paperminer/workflows/batch_filter.py`
- `miners/paperminer/decision/filter_engine.py`（构造参数缺省时**内部再 `load_config()` 并 new**）
- `oligo/api/server.py`：**lifespan 里 `app.state.llm_client = OpenAICompatibleClient()`**，但 `/v1/agent/invoke` 里**又重新 `OpenAICompatibleClient(...)`**（按请求体里的 key/base_url/model），**应用状态里的 client 基本成摆设**。

**基建层自身的服务定位器味道**

`client.py` 中 `OpenAICompatibleClient.__init__` **无条件** `settings = load_config()`，再合并显式参数。即：**所谓「注入」只是覆盖默认，每次构造仍全局读配置**——这不是纯洁的依赖注入，而是 **Service Locator + 参数补丁**。

**分级**

| 级别 | 结论 |
|------|------|
| **Fatal** | Oligo 路由里 **lifespan 挂载的 client 与请求路径实际使用的 client 不一致**，属于**控制流与资源生命周期撒谎**；观测、连接池、限流若将来挂在 `app.state` 上会直接失效。 |
| **Critical** | `filter_engine`、`batch_filter`、脚本层多处重复「从 `Settings` 抠 api_key/base_url/model」样板，**无统一 Factory / 无测试替身注入点**（虽有可选参数，但默认路径永远绑死全局配置）。 |
| **Major** | `optics/engine.py` 仅**类型注解**依赖 `OpenAICompatibleClient`，设计尚可，但上游构造仍分散，**Optics 与 Filter 两条业务线无共享的「会话级」客户端策略**。 |

---

## 审查维度 3：Prompt 的癌变与擦屁股循环 (The Prompt Malignancy)

### 3.1 配置碎片化

`prompts/` 下可见结构：`base/`、`tasks/`、`templates/`、`optics/`、`docs/`。  
**12 个文件**里混有：

- 真正 Jinja 模板（`.j2`）
- **文档性质**的 `docs/review_zero_v3.md`（与 `base/reviewer_zero.j2` 是否同源？若不同步则是**双写地狱**）

**分级**：**Major** — 目录按「形态」而非「生命周期或领域」划分，`docs` 与可执行模板并列，**知识源与运行源未分离**。

---

### 3.2 运行时加载与硬编码拼接

- **`PromptManager`**（`prompt_manager.py`）：默认 `Path(__file__).resolve().parents[3] / "prompts"` —— **与文件在树中深度强耦合**；若将来移动 `llm_gateway` 包，静默断链。
- **`filter_engine`**：通过 `render("base/reviewer_zero.j2")` + `render("tasks/filter_task.j2", paper=..., json_schema=...)` 组装调用链，尚属正常。
- **`optics/loader.py`**：大量 **JSON 输出军规、persona、内置透镜**以 **Python 三引号字符串** 形式存在（`_JSON_MATH`、`_builtin_lens_configs` 等），与 `prompts/optics/*.j2` **双轨并存**——**透镜「真身」到底在 YAML/磁盘还是代码里**，需要读 loader 全篇才能回答。

**分级**

| 级别 | 结论 |
|------|------|
| **Critical** | Optics 的「提示词资产」分裂为 **代码内嵌长字符串** 与 **prompts 目录**，违反单一事实来源；演进时极易出现 **磁盘改了、内置默认值没改** 的幽灵行为。 |
| **Major** | `PromptManager.render` 的路径校验（禁 `..`）是正派的，但 **模板根目录锚点依赖 `parents[3]`** 属于脆弱魔法数。 |

---

### 3.3 元数据与模板：谁在擦屁股？

**`VaultWriter`**（`vault_writer.py`）

- `write_knowledge_node` / `write_deep_read_node`：把 `paper`、`analysis`/`atlas`、`note_asset_basename`、`current_date` 一股脑丢给 Jinja；**后端侧格式化较少**。
- **脏活被推到模板**：`prompts/templates/deep_read_node.j2` 前文 YAML frontmatter 中，对 `arxiv_id`、`short_moniker`、`title` 使用  
  `| replace('"', '\\"')` **防 YAML 炸膛**——这是典型的 **在模板层用字符串替换修补序列化**，而不是在 Pydantic/`yaml.safe_dump` 层输出结构化元数据。

**`PaperFilterEngine._validate_prompt_boundary`**（`filter_engine.py`）

- 在 Python 里检查 `system_prompt` 是否含 `"Reviewer Zero"`、`"[THE TRIAGE PROTOCOL"`，用户侧是否含 `"[USER PROFILE & RESEARCH STANCE]"` 等——**与 Jinja 模板内容硬编码耦合**。模板改一个词，**运行时校验就炸**，属于 **跨层字符串契约**。

**`PromptManager` 注册 `tojson` 过滤器**（`prompt_manager.py`），供模板把 Pydantic 子结构 `| tojson` 输出；**把序列化责任放在模板**——短期省事，长期难以做 schema 版本化与审计。

**分级**

| 级别 | 结论 |
|------|------|
| **Fatal** | **Prompt 边界校验**与 **Jinja 模板字面量**锁死，构成**最脆弱的集成测试替代品**；任何产品化改版 prompts 都要改 Python 常量。 |
| **Critical** | Deep Read frontmatter 依赖 Jinja 的 `replace` 逃过引号，**不是安全模型，是踩钢丝**；若字段含其它 YAML 特殊字符，仍会翻车。 |
| **Major** | `VaultWriter` 在 `write_deep_read_node` 里分支 `atlas.is_survey` 选模板与后缀，**部分业务规则在 Python，部分在模板**，责任切分随意。 |

---

## 全局汇总表（按严重度）

| 严重度 | 主题 | 重灾区文件 / 位置 |
|--------|------|-------------------|
| **Fatal** | 三套垂直切片无法统一为 DDD 边界 | `src/miners/paperminer/**`、`src/optics/**`、`src/oligo/**` |
| **Fatal** | CLI 承担编排 + 寻址 + 资源构造 | `scripts/run_lens.py`、`scripts/run_single.py` |
| **Fatal** | filtered 与 router 的目录契约靠注释对齐 | `run_lens.py` 中 `_FILTERED_VERDICT_DIRS` ↔ `file_router.py` |
| **Fatal** | Prompt 与 Python 字符串强耦合校验 | `filter_engine.py` `_validate_prompt_boundary` ↔ `base/reviewer_zero.j2`、`tasks/filter_task.j2` |
| **Fatal** | Oligo lifespan client 与请求路径实际 client 不一致 | `oligo/api/server.py` `lifespan` vs `agent_invoke` |
| **Critical** | arXiv/ basename 规则三处分叉 | `filename.py`、`vault_indexer.py`、`run_lens.py` |
| **Critical** | `OpenAICompatibleClient` 每次 `load_config()` + 多点手写构造 | `client.py`、`batch_filter.py`、`run_single.py`、`run_lens.py`、`filter_engine.py`、`server.py` |
| **Critical** | Optics 提示词内嵌 Python vs 外部 prompts 双轨 | `optics/loader.py` vs `prompts/optics/*.j2` |
| **Major** | 多处 `rglob`/`read_text` 无统一仓储抽象 | `paper_loader.py`、`vault_indexer.py`、`run_lens.py`、`obsidian_search.py` |
| **Major** | `prompts/docs/*.md` 与 `.j2` 混放 | `prompts/docs/review_zero_v3.md` |
| **Major** | 模板层 `replace`/`tojson` 承担序列化 | `templates/deep_read_node.j2`、`prompt_manager.py` |

---

## 结语

这座仓库不是「演进中的单体」，而是**四次紧急重构后粘在 `crucible_core` 名下的三座城市**：一座自带城墙的 PaperMiner 城邦、一座 streaming 剧院 Oligo、一片 Optics 荒原上几根写着 YAML 的电线杆。  
**路径与字符串契约在脚本、索引器、路由之间口口相传**；**LLM 客户端在 lifespan 里装睡、在请求里另娶**；**提示词一半在磁盘上一半在 Python 的肠子里**，再用 `_validate_prompt_boundary` 当缝合线。  
若说这是「可维护的架构」，那法医报告只能写：**死因是多处软组织撕裂导致的失血，现场留有大量 Jinja 与正则的搏斗痕迹。**

---

*文档生成自架构审计归档；后续若 codebase 有重大重构，应重新跑静态审阅并更新本文件。*
