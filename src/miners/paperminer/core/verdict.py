"""Verdict data models for paper filtering (PaperMiner domain)."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


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
        description="Key takeaways from their ablation studies. What specific component did they remove or tweak, and how much did performance drop? (e.g., 'Removing the Time-aware Query Expansion caused a 15% drop in temporal reasoning accuracy.')"
    )
