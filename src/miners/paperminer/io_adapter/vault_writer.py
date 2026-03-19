"""Vault writer for persisting knowledge nodes into Obsidian inbox."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.core.config import Settings
from src.llm_gateway.prompt_manager import PromptManager

from ..core.paper import Paper
from ..core.verdict import PaperAnalysisResult

logger = logging.getLogger(__name__)

_ILLEGAL_FILENAME_CHARS = r'[\\/:*?"<>|]'
_MAX_BASENAME_LENGTH = 100


class VaultWriter:
    """Render and persist paper knowledge nodes as markdown files."""

    def __init__(self, settings: Settings, prompt_manager: PromptManager) -> None:
        configured_inbox = settings.require_path("inbox_folder")
        if not configured_inbox.is_absolute():
            raise ValueError("`inbox_folder` must resolve to an absolute path.")
        self.vault_inbox_dir = configured_inbox
        self.prompt_manager = prompt_manager
        self.vault_inbox_dir.mkdir(parents=True, exist_ok=True)

    def write_knowledge_node(self, paper: Paper, analysis: PaperAnalysisResult) -> Path:
        """Render `knowledge_node.j2` and write it to Obsidian inbox."""
        rendered = self.prompt_manager.render(
            "templates/knowledge_node.j2",
            paper=paper,
            analysis=analysis,
        )
        target_dir = self.vault_inbox_dir / analysis.verdict.value.replace(" ", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_moniker = self._sanitize_filename(analysis.short_moniker)
        if safe_moniker:
            output_path = target_dir / f"{paper.id}-{safe_moniker}.md"
        else:
            safe_paper_id = self._sanitize_filename(paper.id)
            output_path = target_dir / f"{safe_paper_id}.md"
        output_path.write_text(rendered, encoding="utf-8")
        logger.info("Knowledge node written to: %s", output_path)
        return output_path

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        """Convert input text into a cross-platform-safe markdown filename fragment."""
        normalized = re.sub(_ILLEGAL_FILENAME_CHARS, "_", title).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized[:_MAX_BASENAME_LENGTH].rstrip(" .")
        return normalized
