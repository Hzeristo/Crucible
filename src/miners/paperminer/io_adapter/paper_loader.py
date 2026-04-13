"""IO adapter for promoting and loading paper markdown files."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..core.paper import Paper, PaperMetadata
from .arxiv_fetcher import ArxivFetcher

logger = logging.getLogger(__name__)


class PaperLoader:
    """Handle markdown file promotion (Rescue) and Paper materialization."""

    def extract_and_clean(
        self, raw_paper_dir: Path, clean_dir: Path, paper_stem: str
    ) -> Path:
        """
        Search for the true Markdown file within a messy MinerU output folder,
        and copy it cleanly to the final directory.

        Args:
            raw_paper_dir: The root folder MinerU generated for this paper (contains /auto, /images etc).
            clean_dir: Target clean directory for processed markdown files.
            paper_stem: The original name of the paper (without .pdf), used for precise matching.

        Returns:
            Path to the promoted markdown file under clean_dir.
        """
        if not raw_paper_dir.exists() or not raw_paper_dir.is_dir():
            raise FileNotFoundError(f"Raw paper directory not found: {raw_paper_dir}")

        clean_dir.mkdir(parents=True, exist_ok=True)
        final_clean_md = clean_dir / f"{paper_stem}.md"

        if final_clean_md.exists():
            logger.info(
                "Target markdown already exists in clean vault: %s", final_clean_md.name
            )
            return final_clean_md

        md_files = list(raw_paper_dir.rglob("*.md"))
        if not md_files:
            raise FileNotFoundError(
                f"Failed extraction: No markdown files found recursively in {raw_paper_dir}"
            )

        best_match = None
        for md in md_files:
            if md.stem == paper_stem:
                best_match = md
                break

        if not best_match:
            best_match = md_files[0]
            logger.warning(
                "Could not find an exact match for '%s.md'. Falling back to '%s'",
                paper_stem,
                best_match.name,
            )

        try:
            shutil.copy2(best_match, final_clean_md)
            logger.info("Extracted: %s -> %s", best_match.name, final_clean_md.name)
        except OSError as exc:
            logger.error(
                "Failed to extract markdown from %s", raw_paper_dir, exc_info=True
            )
            raise RuntimeError(
                f"IO Error during promotion. source={best_match}, target={final_clean_md}"
            ) from exc

        return final_clean_md

    def load_paper(self, clean_md: Path) -> Paper:
        """
        Read clean markdown and construct a strict Paper model.

        Args:
            clean_md: Clean markdown file path.

        Returns:
            Materialized Paper instance.
        """
        if clean_md.suffix.lower() != ".md":
            raise ValueError(f"Expected a .md file, got: {clean_md}")
        if not clean_md.exists():
            raise FileNotFoundError(f"Clean markdown not found: {clean_md}")

        try:
            raw_text = clean_md.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            logger.error("Failed to read markdown file: %s", clean_md, exc_info=True)
            raise RuntimeError(
                f"Failed to read markdown file: {clean_md.name}"
            ) from exc

        paper_id = clean_md.stem
        year: str | None = None
        authors: str | None = None

        if ArxivFetcher._extract_arxiv_core_id(paper_id) is not None:
            logger.info("Fetching metadata for %s...", paper_id)
            fetcher = ArxivFetcher(timeout_seconds=5)
            meta = fetcher.fetch_single_metadata(paper_id)
            if meta:
                year = meta["year"]
                authors = meta["authors"]
            else:
                year = "Unknown"
                authors = "Unknown"

        return Paper(
            id=paper_id,
            type="arxiv_paper",
            title=paper_id,
            content_path=clean_md.resolve(),
            raw_text=raw_text,
            year=year,
            authors=authors,
            metadata=PaperMetadata(extracted_from="MinerU"),
        )

    def load_clean_md(self, md_path: Path) -> Paper:
        """Load one already-clean markdown file as a Paper object."""
        return self.load_paper(md_path)
