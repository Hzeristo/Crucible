"""Paper entity for PaperMiner domain."""

from pydantic import BaseModel, Field
from typing import Literal

SourceType = Literal[
    "arxiv_paper", "github_repo", "tech_blog", "book_chapter", "markdown"
]


class Paper(BaseModel):
    """记录一篇 Paper 的信息"""

    id: str
    type: SourceType = Field(
        default="arxiv_paper", description="决定了 LLM 将以何种视角审视此文本"
    )
    title: str
    content_path: str  # 本地 Markdown 路径
    raw_text: str = Field(repr=False)  # 不打印大段文本
    metadata: dict = Field(default_factory=dict)
