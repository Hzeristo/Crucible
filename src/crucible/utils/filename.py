"""跨平台安全的文件名生成与清洗."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def compute_fancy_basename(paper: "Paper", analysis: "PaperAnalysisResult | None") -> str:
    """
    计算统一的归档文件名前缀（不含扩展名）。
    与 VaultWriter 的命名逻辑一致：paper.id + short_moniker。
    """
    if analysis is not None:
        safe_moniker = sanitize_filename(analysis.short_moniker)
        if safe_moniker:
            return f"{paper.id}-{safe_moniker}"
    return sanitize_filename(paper.id)
