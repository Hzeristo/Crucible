"""跨平台安全的文件名生成与清洗."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.optics.schema import DeepReadAtlas
    from src.miners.paperminer.core.paper import Paper
    from src.miners.paperminer.core.verdict import PaperAnalysisResult

_ILLEGAL_FILENAME_CHARS = r'[\\/:*?"<>|]'
_MAX_BASENAME_LENGTH = 100


def sanitize_filename(title: str) -> str:
    """Convert input text into a cross-platform-safe filename fragment."""
    normalized = re.sub(_ILLEGAL_FILENAME_CHARS, "_", title).strip()
    normalized = re.sub(r"\s+", "_", normalized)  # collapse whitespace to underscore
    normalized = normalized[:_MAX_BASENAME_LENGTH].rstrip("_.")
    return normalized


def compute_fancy_basename(
    paper: "Paper",
    analysis: "PaperAnalysisResult | DeepReadAtlas | None",
) -> str:
    """
    统一的笔记 / PDF 资产 basename（无扩展名）：``{paper.id}-{short_moniker}``（与 Obsidian 一致）。
    ``short_moniker`` 经 :func:`sanitize_filename` 清洗；无 analysis 时退化为 ``paper.id``。
    """
    if analysis is not None:
        raw = getattr(analysis, "short_moniker", None)
        if raw:
            safe_moniker = sanitize_filename(raw)
            if safe_moniker:
                return f"{paper.id}-{safe_moniker}"
    return sanitize_filename(paper.id)
