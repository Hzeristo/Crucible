"""Paper entity for PaperMiner domain."""

from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    content_path: Path  # 本地 Markdown 路径
    raw_text: str = Field(repr=False)  # 不打印大段文本
    year: str | None = Field(
        default=None, description="Official submission year from arXiv API when available."
    )
    authors: str | None = Field(
        default=None,
        description='Official author list from arXiv API (comma-separated), when available.',
    )
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
