"""Workflow for batch PDF ingestion through MinerU."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.crucible.core.config import Settings

from ..io_adapter.paper_loader import PaperLoader
from ..io_adapter.paper2md import MineruClient

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - graceful fallback when tqdm is missing.

    def tqdm(iterable, **_kwargs):  # type: ignore[no-redef]
        return iterable


logger = logging.getLogger(__name__)


def _normalize_against_project(path: Path, settings: Settings) -> Path:
    """Normalize path and avoid CWD-dependent behavior."""
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (settings.project_root / expanded).resolve()


def run_pdf_ingestion(
    input_dir: Path,
    output_dir: Path,
    clean_dir: Path | None = None,
) -> int:
    """Convert PDFs to raw markdown, extract clean markdown, and return success count."""
    settings = Settings()
    normalized_input = _normalize_against_project(input_dir, settings)
    normalized_raw_output = _normalize_against_project(output_dir, settings)
    pm = settings.paper_miner_or_default
    target_clean_dir = (
        clean_dir
        or pm.md_papers_dir
        or (settings.project_root / "papers" / "md_papers")
    )
    normalized_clean_dir = _normalize_against_project(target_clean_dir, settings)

    if not normalized_input.exists() or not normalized_input.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {normalized_input}")

    pdf_files = sorted(normalized_input.glob("*.pdf"))
    logger.info("Found %s PDF files in %s", len(pdf_files), normalized_input)
    if not pdf_files:
        logger.info("No PDF files found in %s", normalized_input)
        return 0

    client = MineruClient(output_root=normalized_raw_output)
    paper_loader = PaperLoader()
    total = len(pdf_files)
    success_count = 0
    progress = tqdm(pdf_files, total=total, unit="pdf")

    for idx, pdf_path in enumerate(progress, start=1):
        progress.set_description(f"[{idx}/{total}] Ingesting {pdf_path.name}")
        try:
            raw_md = client.convert(pdf_path)
            paper_stem = pdf_path.stem
            raw_paper_dir = normalized_raw_output / paper_stem
            if not raw_paper_dir.exists() or not raw_paper_dir.is_dir():
                raw_paper_dir = raw_md.parent

            paper_loader.extract_and_clean(
                raw_paper_dir=raw_paper_dir,
                clean_dir=normalized_clean_dir,
                paper_stem=paper_stem,
            )

            # Raw MinerU folder is no longer needed after clean markdown extraction.
            canonical_raw_dir = normalized_raw_output / paper_stem
            cleanup_target = (
                canonical_raw_dir if canonical_raw_dir.exists() else raw_paper_dir
            )
            try:
                if cleanup_target.exists() and cleanup_target.is_dir():
                    shutil.rmtree(cleanup_target)
                    logger.info("Removed raw folder after cleaning: %s", cleanup_target)
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to cleanup raw folder for %s: %s",
                    pdf_path.name,
                    cleanup_exc,
                )

            success_count += 1
        except Exception as exc:
            logger.error("PDF ingestion failed for %s: %s", pdf_path, exc)
            continue

    return success_count
