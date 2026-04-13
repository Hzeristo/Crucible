"""Optics：Lens 配置与结构化输出受体（Pydantic V2，extra 一律 forbid）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LensConfig(BaseModel):
    """单次并发 LLM 调用的透镜定义；`output_schema_name` 为可反射加载的受体类名。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., min_length=1)
    system_prompt: str
    output_schema_name: str = Field(..., min_length=1)
    description: str


class MathArchExtraction(BaseModel):
    """方法论与结构：方程、伪代码、架构标签。"""

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
    """实验与评测：数据集、基线、指标与消融焦点。"""

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
    """记忆与上下文物理：遗忘机制与窗口技巧。"""

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


# --- Survey 专用受体（拒绝文献堆砌，抽取拓扑、共识痛点与结构空白） ---


class TaxonomyExtraction(BaseModel):
    """综述：领域切分维度与核心类别（每类须绑定论文内技术边界释义）。"""

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
    """综述：跨工作共识的技术范式通病与硬瓶颈。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    major_limitations: list[str] = Field(
        default_factory=list,
        description="范式级通病与硬瓶颈；Prompt 要求每条高度概括且不少于约 20 字。",
    )


class FutureDirectionGap(BaseModel):
    """综述：单条未来方向与其背后的技术断层（非标题枚举）。"""

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
    """综述：作者明确指出的、尚未被填补的架构或理论空位。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    future_directions: list[FutureDirectionGap] = Field(
        default_factory=list,
        description=(
            "每条为独立方向；须写清使该问题仍开放的架构或理论局限，而非复述小节标题。"
        ),
    )


class DeepReadAtlas(BaseModel):
    """深读总装箱：标识元数据 + 实验型与综述型受体（未跑通的 Lens 对应字段为 None）。"""

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
