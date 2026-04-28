"""全系统唯一 Pydantic 数据字典（PaperMiner / Optics / Oligo / 工作流）。"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Paper / Triage ---

SourceType = Literal[
    "arxiv_paper", "github_repo", "tech_blog", "book_chapter", "markdown"
]


class PaperMetadata(BaseModel):
    """Typed metadata payload attached to a paper."""

    model_config = ConfigDict(extra="forbid")

    extracted_from: str | None = None
    score: int | None = None
    reason: str | None = None
    year: str | None = None
    authors: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class Paper(BaseModel):
    """记录一篇 Paper 的信息"""

    id: str
    type: SourceType = Field(
        default="arxiv_paper", description="决定了 LLM 将以何种视角审视此文本"
    )
    title: str
    content_path: Path
    raw_text: str = Field(repr=False)
    year: str | None = Field(
        default=None,
        description="Official submission year from arXiv API when available.",
    )
    authors: str | None = Field(
        default=None,
        description="Official author list from arXiv API (comma-separated), when available.",
    )
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)


class VerdictDecision(str, Enum):
    """Final decision labels for paper triage."""

    REJECT = "Reject"
    SKIM = "Skim"
    MUST_READ = "Must Read"


class PaperAnalysisResult(BaseModel):
    """Structured analysis result returned by LLM-based reviewer."""

    model_config = ConfigDict(extra="forbid")

    verdict: VerdictDecision = Field(
        description='Decision: "Reject" / "Skim" / "Must Read".'
    )
    short_moniker: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Must be EXACTLY ONE capitalized proper noun representing the core system/model name "
            "(e.g., 'HippoRAG', 'MemGPT', 'Titans'). DO NOT add descriptive words like 'Architecture', "
            "'Graph', or 'Memory'. If no distinct proper noun exists, invent a SINGLE capitalized "
            "portmanteau word. Exclude the raw paper ID or dates."
        ),
    )
    score: int = Field(
        ge=0,
        le=10,
        description="Overall score. Normal range is 1-10; 0 is reserved for degraded fallback.",
    )
    novelty_delta: str = Field(
        min_length=1, description="Compared with baseline, where is the gain?"
    )
    mechanism_summary: str = Field(min_length=1, description="Core mechanism summary.")
    critical_flaws: list[str] = Field(
        default_factory=list, description="Critical flaws and attack points."
    )
    baseline_models: list[str] = Field(
        default_factory=list,
        description=(
            "Models used as baselines (e.g., ['GPT-4', 'Llama-3-8B']). "
            "Empty list if none."
        ),
    )
    evaluation_datasets: list[str] = Field(
        default_factory=list,
        description=(
            "Benchmarks/Datasets tested on (e.g., ['MMLU', 'WebArena']). "
            "Empty list if none."
        ),
    )
    core_algorithm_steps: list[str] = Field(
        default_factory=list,
        description=(
            "Extremely concise step-by-step breakdown of the proposed "
            "mechanism/architecture. Empty list if none."
        ),
    )
    experimental_setup: str = Field(
        default="Not specified.",
        description=(
            "Forensic-grade execution pipeline for experiments: NOT a high-level summary. "
            "Dense bulleted report covering, when present or conspicuously absent: "
            "(1) Context ingestion—batch full-history stuffing vs true incremental/turn-by-turn; "
            "(2) Environment realism—mock static QA-style evaluation vs dynamic interactive envs; "
            "(3) Prompting hacks—oracle leakage, asymmetric few-shot vs baselines, forced CoT; "
            "(4) Memory state management—exact update mechanics (e.g. full recompute every N turns vs silent vector DB append). "
            "Use newline characters (\\n in JSON) between sections/bullets so downstream Markdown renders multiple lines; "
            "do not collapse into one uninterrupted paragraph unless the paper gives almost no detail."
        ),
    )
    ablation_findings: list[str] = Field(
        default_factory=list,
        description=(
            "Key takeaways from their ablation studies. What specific component did they remove or tweak, "
            "and how much did performance drop? (e.g., 'Removing the Time-aware Query Expansion caused a 15% drop in temporal reasoning accuracy.')"
        ),
    )


# --- Optics ---


class LensConfig(BaseModel):
    """单次并发 LLM 调用的透镜定义；`output_schema_name` 为可反射加载的受体类名。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., min_length=1)
    system_prompt: str
    output_schema_name: str = Field(..., min_length=1)
    description: str


class MathArchExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    architecture_narrative: str = Field(
        default="",
        description=(
            "Design philosophy and data flow: 2–3 bullet lines each led by a bold key concept "
            "(per prompt)—not one wall of text."
        ),
    )
    core_equations: list[str] = Field(default_factory=list)
    pseudo_code: str = ""
    architecture_type: list[str] = Field(default_factory=list)


class EvalRigorExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    baselines: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    metrics_used: list[str] = Field(default_factory=list)
    ablation_target: str = Field(
        default="",
        description=(
            "Deep, critical analysis of ablations and empirical setup: what removing the core "
            "component does to metrics; use 2–3 bullet lines each led by a bold key concept per prompt."
        ),
    )


class MemoryPhysicsExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    mechanics_deep_dive: str = Field(
        default="",
        description=(
            "Extensive explanation of memory states, context bounds, overwrites; 2–3 bullet lines "
            "each led by a bold key concept per prompt—not one wall of text."
        ),
    )
    forgetting_mechanism: str = Field(
        default="",
        description="Concrete prose on forgetting / state handling (not a sparse label).",
    )
    context_window_tricks: str = Field(
        default="",
        description=(
            "Descriptive prose on context-window and state tricks (not a tag list); paragraph-style."
        ),
    )


class TaxonomyExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    classification_axes: list[str] = Field(
        default_factory=list,
        description="作者用来结构化学术领域的抽象维度（非模型枚举）；可短。",
    )
    core_categories: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "键为类别名；值必须为 1–3 句高密度技术释义，说明在 LLM/Agent 语境下本文如何显式定义或实现该类别，"
            "以及与其它类别区分的架构边界（非目录短语）。"
        ),
    )


class ConsensusAndBottlenecks(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    major_limitations: list[str] = Field(
        default_factory=list,
        description="范式级通病与硬瓶颈；Prompt 要求每条高度概括且不少于约 20 字。",
    )


class FutureDirectionGap(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    direction: str = Field(
        ...,
        min_length=1,
        description="该研究坑位或方向的短名称（可对应 Future Work 小节，但须单独成条）。",
    )
    technical_void: str = Field(
        ...,
        min_length=1,
        description=(
            "指出现有理论与架构上仍未解决的具体断层：为何难、卡在哪（如可扩展性、可组合性、评测缺口），"
            "禁止仅堆砌 buzzword。"
        ),
    )


class StructuralGaps(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    future_directions: list[FutureDirectionGap] = Field(
        default_factory=list,
        description=(
            "每条为独立方向；须写清使该问题仍开放的架构或理论局限，而非复述小节标题。"
        ),
    )


class DeepReadAtlas(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    arxiv_id: str = Field(..., min_length=1)
    short_moniker: str = Field(..., min_length=1, max_length=64)
    title: str | None = None
    is_survey: bool = False

    math_arch: MathArchExtraction | None = None
    eval_rigor: EvalRigorExtraction | None = None
    memory_physics: MemoryPhysicsExtraction | None = None

    taxonomy: TaxonomyExtraction | None = None
    consensus_bottlenecks: ConsensusAndBottlenecks | None = None
    structural_gaps: StructuralGaps | None = None


# --- Oligo ---


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"] = Field(
        ..., description="The author of this message."
    )
    content: str = Field(..., description="The textual content of the message.")
    tool_call_id: str | None = Field(
        default=None,
        description="Reserved for OpenAI Function Calling (tool result messages).",
    )
    name: str | None = Field(
        default=None,
        description="Reserved for OpenAI Function Calling (tool name).",
    )


class SkillDefinition(BaseModel):
    """`~/.chimera/skills/{skill_id}.json` 的纯净定义（不含使用统计）。

    统计见 `~/.chimera/skill_stats.json`，由 `SkillStatsService` 维护。
    旧文件中若仍含 ``usage_count`` / ``success_rate`` / ``avg_tokens`` 等键，
    加载时会被忽略（``extra="ignore"``）。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""
    system_override: str = Field(..., min_length=1)
    allowed_tools: list[str] | None = None
    category: str = Field(default="general", min_length=1)
    target_paper_type: list[str] = Field(default_factory=list)
    expected_output_format: str | None = None
    version: str = Field(default="1.0.0", min_length=1)
    last_updated: str | None = None


class AgentInvokeRequest(BaseModel):
    """Agent 调用请求体（与 Astrocyte ``OligoAgentRequest`` JSON 严格同构）。

    **JSON 顶层键**（snake_case，与 Pydantic 字段名一致；与 ``llm_client.rs`` 中
    ``OligoAgentRequest`` 的 serde 默认字段名一一对应）::

        api_key, base_url, model_name, persona_id?, system_core,
        skill_override?, skill_id?, allowed_tools?, persona?, authors_note?,
        temperature?, messages

    可选字段在 Rust 侧为 ``None`` 时使用 ``skip_serializing_if`` **省略键**；
    FastAPI 解析时等价于 Python 侧 ``None``。``messages`` 元素为 ``ChatMessage``：
    最少包含 ``role``、``content``；未传的 ``tool_call_id`` / ``name`` 视为 ``null``。

    Prompt Injection Hierarchy (L1 > L2 > L3):
    - L1 (System Core): Router/Skill system prompts (highest priority)
    - L2 (Persona): User-selected persona (e.g., BB's sarcastic style)
    - L3 (Author's Note): Ephemeral, single-session instruction (lowest priority)
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(
        ...,
        description="LLM API key from gateway (may be empty if server defaults apply).",
    )
    base_url: str = Field(..., description="Chat/completions API base URL from gateway.")
    model_name: str = Field(..., description="Model id from gateway.")
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Temperature for this Oligo request. If None, use ChimeraConfig llm.working.temperature.",
    )
    persona_id: str | None = Field(
        default=None,
        description="Optional persona id for logging only; not used for routing decisions.",
    )
    system_core: str = Field(
        ...,
        description=(
            "L1: Core system baseline from the gateway (e.g. active persona `system_prompt` only). "
            "Do not embed Author's Note or persona override here; use `persona` / `authors_note`."
        ),
    )
    skill_override: str | None = Field(default=None)
    skill_id: str | None = Field(
        default=None,
        description=(
            "Active skill file stem (~/.chimera/skills/{skill_id}.json)；"
            "统计写入 ~/.chimera/skill_stats.json，与 skill_override 正交。"
        ),
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        description="If set, only these tool names may execute in the router/tool loop; None means no restriction.",
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="Clean user/assistant transcript and current turn (no gateway-prefixed system).",
    )
    persona: str | None = Field(
        None,
        description="L2: Persona override. Injected in Final Stream stage as [PERSONA OVERRIDE].",
    )
    authors_note: str | None = Field(
        None,
        description="L3: Ephemeral instruction. Injected after all system prompts as [AUTHOR'S NOTE].",
    )


# --- Oligo Tool Execution ---


class ToolCallStatus(str, Enum):
    """Discrete states for permission checks and execution outcomes of one tool call."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class PlannedToolCall(BaseModel):
    """Parsed tool invocation (XML / CMD) with optional allowlist gate and structured args."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        description="Short unique id per invocation (e.g. UUID fragment) for logs and UI correlation.",
    )
    tool_name: str = Field(..., description="Registry tool name from the CMD tag.")
    raw_args: str = Field(
        ...,
        description="Literal text inside the CMD parentheses from the model output.",
    )
    args: dict[str, Any] = Field(
        ...,
        description="JSON object parsed from raw_args (same semantics as agent-side parsing).",
    )
    allowed: bool = Field(
        ...,
        description="True if policy allows execution for this tool name in the current context.",
    )
    deny_reason: str | None = Field(
        default=None,
        description="Human-readable reason when allowed is False; None when allowed.",
    )
    repairs_applied: list[str] = Field(
        default_factory=list,
        description="Conservative format-only repairs applied to raw_args before JSON parse (TP.3).",
    )


class ExecutedToolResult(BaseModel):
    """Immutable record of one tool run: inputs, status, optional raw/wash text, timing."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(..., description="Matches PlannedToolCall.id for this execution.")
    tool_name: str = Field(..., description="Tool that was invoked.")
    args: dict[str, Any] = Field(
        ...,
        description="Structured arguments used for execution (copy of or canonical parse).",
    )
    status: ToolCallStatus = Field(..., description="Permission or runtime outcome.")
    raw_result: str | None = Field(
        default=None,
        description="Unwashed tool output string when execution ran.",
    )
    washed_result: str | None = Field(
        default=None,
        description="LLM-compressed or post-processed text for downstream prompts.",
    )
    error_message: str | None = Field(
        default=None,
        description="Error or timeout message when status is ERROR or TIMEOUT.",
    )
    elapsed_ms: int | None = Field(
        default=None,
        description="Wall time for the execute step in milliseconds, if measured.",
    )


class OligoAgentConfig(BaseModel):
    """Tunable tool execution deadlines and wash routing policy for ChimeraAgent."""

    model_config = ConfigDict(extra="forbid")

    tool_execution_deadline_seconds: float = Field(
        default=45.0,
        ge=1.0,
        le=600.0,
        description="Per-tool asyncio.wait_for ceiling for registry/vault calls.",
    )
    wash_min_chars: int = Field(
        default=1200,
        ge=0,
        description="Minimum raw output length before FORCE wash tools may invoke LLM wash.",
    )
    bypass_wash_tools: set[str] = Field(
        default_factory=lambda: {
            "search_vault_attribute",
            "metadata_lookup",
            "planner_json",
        },
        description="Tool names that skip LLM wash; copy raw into washed_result.",
    )
    force_wash_tools: set[str] = Field(
        default_factory=lambda: {
            "search_vault",
            "web_search",
            "read_markdown",
        },
        description="Tool names that may invoke LLM wash when output is long enough.",
    )


# --- Batch workflow ---


class BatchMustReadItem(BaseModel):
    score: int
    id: str
    paper_id: str
    short_moniker: str
    filename: str
    title: str
    novelty: str


class BatchFilterStats(BaseModel):
    total: int = 0
    must_read: int = 0
    skim: int = 0
    reject: int = 0
    errors: int = 0
    processed_ids: list[str] = Field(default_factory=list)
    must_read_titles: list[str] = Field(default_factory=list)
    must_read_items: list[BatchMustReadItem] = Field(default_factory=list)
    source_dir: Path | None = None


# --- Oligo PromptComposer ---


class PromptStage(str, Enum):
    """Prompt 注入的目标阶段"""

    ROUTER = "router"  # Router 探针阶段的 system
    FINAL = "final"  # Final 推流阶段的 system
    BOTH = "both"  # 两个阶段都注入
    MESSAGE_INJECTION = "message_injection"  # 注入到 messages 而非 system


class PromptComponent(BaseModel):
    """单个 Prompt 片段的元数据"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="唯一标识, 如 'router_core', 'skill_directive'")
    stage: PromptStage
    priority: int = Field(
        description="数字越大越靠前(更显眼). 100=核心规则, 50=任务约束, 10=guardrail"
    )
    cacheable: bool = Field(
        default=True,
        description="是否属于 stable prefix. 时间戳/会话 ID 等动态内容应为 False",
    )
    template: str = Field(description="prompt 文本, 可含 {variable} 占位符")


class ToolSpec(BaseModel):
    """工具的元数据"""

    name: str = Field(description="工具名, 与 TOOL_REGISTRY 的 key 一致")
    description: str = Field(description="一行简介, 用于 router prompt")
    args_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-schema-like 描述, 形如 {'query': {'type': 'str', 'required': True}}",
    )
    concurrency_safe: bool = Field(
        default=False,
        description="True=可与其他 concurrency_safe 工具并行. False=必须串行(可能改文件状态).",
    )
    long_running: bool = Field(
        default=False,
        description="True=立即返回 task_id 的异步工具(arxiv_miner / daily_paper_pipeline)",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="可选: 1-2 个调用示例, 用于强化 router 的 in-context learning",
    )

    model_config = ConfigDict(extra="forbid")
