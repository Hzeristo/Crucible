"""Render and write Obsidian knowledge / deep-read nodes."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.crucible.core.config import ChimeraConfig
from src.crucible.core.naming import compute_fancy_basename
from src.crucible.core.schemas import DeepReadAtlas, Paper, PaperAnalysisResult
from src.crucible.ports.prompts.jinja_prompt_manager import PromptManager

logger = logging.getLogger(__name__)

_DEEP_READ_SUBDIR = "01_Deep_Reads"


class VaultNoteWriter:
    """Render and persist paper knowledge nodes as markdown files."""

    def __init__(self, settings: ChimeraConfig, prompt_manager: PromptManager) -> None:
        self.settings = settings
        configured_inbox = settings.require_path("inbox_folder")
        if not configured_inbox.is_absolute():
            raise ValueError("`inbox_folder` must resolve to an absolute path.")
        self.vault_inbox_dir = configured_inbox
        self.prompt_manager = prompt_manager
        self.vault_inbox_dir.mkdir(parents=True, exist_ok=True)

    def write_knowledge_node(self, paper: Paper, analysis: PaperAnalysisResult) -> Path:
        fancy_basename = compute_fancy_basename(paper, analysis)
        rendered = self.prompt_manager.render(
            "obsidian_tpl/knowledge_node.j2",
            paper=paper,
            analysis=analysis,
            note_asset_basename=fancy_basename,
            current_date=datetime.now().strftime("%Y-%m-%d"),
        )
        target_dir = self.vault_inbox_dir / analysis.verdict.value.replace(" ", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / f"{fancy_basename}.md"
        output_path.write_text(rendered, encoding="utf-8")
        logger.info("[Vault] Knowledge node written to: %s", output_path)
        return output_path

    def write_deep_read_node(
        self,
        paper: Paper,
        atlas: DeepReadAtlas,
        *,
        note_asset_basename: str | None = None,
    ) -> Path:
        stem = note_asset_basename or compute_fancy_basename(paper, atlas)
        if atlas.is_survey:
            template_name = "obsidian_tpl/deep_read_survey_node.j2"
            suffix = "_Survey_Atlas.md"
        else:
            template_name = "obsidian_tpl/deep_read_node.j2"
            suffix = "_Deep_Read.md"
        rendered = self.prompt_manager.render(
            template_name,
            paper=paper,
            atlas=atlas,
            note_asset_basename=stem,
            current_date=datetime.now().strftime("%Y-%m-%d"),
        )
        vault_root = self.settings.require_path("vault_root")
        target_dir = vault_root / _DEEP_READ_SUBDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / f"{stem}{suffix}"
        output_path.write_text(rendered, encoding="utf-8")
        logger.info("[Vault] Deep read node written to: %s", output_path)
        return output_path
