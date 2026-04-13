"""Vault writer for persisting knowledge nodes into Obsidian inbox."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from src.optics.schema import DeepReadAtlas
from src.crucible.core.config import Settings
from src.crucible.llm_gateway.prompt_manager import PromptManager
from src.crucible.utils.filename import compute_fancy_basename

from ..core.paper import Paper
from ..core.verdict import PaperAnalysisResult

logger = logging.getLogger(__name__)

_DEEP_READ_SUBDIR = "01_Deep_Reads"


class VaultWriter:
    """Render and persist paper knowledge nodes as markdown files."""

    def __init__(self, settings: Settings, prompt_manager: PromptManager) -> None:
        self.settings = settings
        configured_inbox = settings.require_path("inbox_folder")
        if not configured_inbox.is_absolute():
            raise ValueError("`inbox_folder` must resolve to an absolute path.")
        self.vault_inbox_dir = configured_inbox
        self.prompt_manager = prompt_manager
        self.vault_inbox_dir.mkdir(parents=True, exist_ok=True)

    def write_knowledge_node(self, paper: Paper, analysis: PaperAnalysisResult) -> Path:
        """Render `knowledge_node.j2` and write it to Obsidian inbox."""
        fancy_basename = compute_fancy_basename(paper, analysis)
        rendered = self.prompt_manager.render(
            "templates/knowledge_node.j2",
            paper=paper,
            analysis=analysis,
            note_asset_basename=fancy_basename,
            current_date=datetime.now().strftime("%Y-%m-%d"),
        )
        target_dir = self.vault_inbox_dir / analysis.verdict.value.replace(" ", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / f"{fancy_basename}.md"
        output_path.write_text(rendered, encoding="utf-8")
        logger.info("Knowledge node written to: %s", output_path)
        return output_path

    def write_deep_read_node(
        self,
        paper: Paper,
        atlas: DeepReadAtlas,
        *,
        note_asset_basename: str | None = None,
    ) -> Path:
        """Render deep-read note under ``{vault_root}/01_Deep_Reads/``.

        综述（``atlas.is_survey``）使用 ``deep_read_survey_node.j2`` 与独立文件名后缀。
        """
        stem = note_asset_basename or compute_fancy_basename(paper, atlas)
        if atlas.is_survey:
            template_name = "templates/deep_read_survey_node.j2"
            suffix = "_Survey_Atlas.md"
        else:
            template_name = "templates/deep_read_node.j2"
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
        logger.info("Deep read node written to: %s", output_path)
        return output_path
