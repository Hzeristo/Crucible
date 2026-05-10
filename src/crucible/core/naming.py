"""跨平台安全的文件名生成与清洗；Vault 文件名解析（无 IO）。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.crucible.core.schemas import DeepReadAtlas, Paper, PaperAnalysisResult

_ILLEGAL_FILENAME_CHARS = r'[\\/:*?"<>|]'
_MAX_BASENAME_LENGTH = 100


def sanitize_filename(title: str) -> str:
    """Convert input text into a cross-platform-safe filename fragment."""
    normalized = re.sub(_ILLEGAL_FILENAME_CHARS, "_", title).strip()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = normalized[:_MAX_BASENAME_LENGTH].rstrip("_.")
    return normalized


def compute_fancy_basename(
    paper: "Paper",
    analysis: "PaperAnalysisResult | DeepReadAtlas | None",
) -> str:
    """
    统一的笔记 / PDF 资产 basename（无扩展名）：``{paper.id}-{short_moniker}``（与 Obsidian 一致）。
    """
    if analysis is not None:
        raw = getattr(analysis, "short_moniker", None)
        if raw:
            safe_moniker = sanitize_filename(raw)
            if safe_moniker:
                return f"{paper.id}-{safe_moniker}"
    return sanitize_filename(paper.id)


def extract_short_moniker_from_note_filename(filename: str, arxiv_id: str) -> str | None:
    """
    Extract moniker from ``{arxiv_id}-*.md`` filename with defensive parsing.
    """
    name = (filename or "").strip()
    if not name:
        return None

    lower_name = name.lower()
    if not lower_name.endswith(".md"):
        return None

    prefix = f"{arxiv_id}-"
    if not name.startswith(prefix):
        return None

    body = name[: -len(".md")]
    moniker = body[len(prefix) :].strip()
    moniker = moniker.strip("-_ ")
    if not moniker:
        return None
    return moniker


def expected_stem(arxiv_id: str, short_moniker: str) -> str:
    """Canonical markdown stem for filtered archive lookup."""
    return f"{arxiv_id}-{short_moniker}"
