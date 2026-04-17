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


class AgentInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(
        ...,
        description="LLM API key from gateway (may be empty if server defaults apply).",
    )
    base_url: str = Field(..., description="Chat/completions API base URL from gateway.")
    model_name: str = Field(..., description="Model id from gateway.")
    persona_id: str | None = Field(
        default=None,
        description="Optional persona id for logging only; not used for routing decisions.",
    )
    system_core: str = Field(
        ...,
        description="The full system prompt payload fetched by Rust (persona baseline).",
    )
    skill_override: str | None = Field(default=None)
    allowed_tools: list[str] | None = Field(
        default=None,
        description="If set, only these tool names may execute in the router/tool loop; None means no restriction.",
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="Clean user/assistant transcript and current turn (no gateway-prefixed system).",
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
    """Parsed <CMD:...> invocation with optional allowlist gate and structured args."""

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
