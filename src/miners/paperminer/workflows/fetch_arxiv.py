"""Workflow for fetching yesterday's latest arXiv PDFs."""

from __future__ import annotations

import logging
from pathlib import Path

from ..io_adapter.arxiv_fetcher import ArxivFetcher

logger = logging.getLogger(__name__)


def run_arxiv_fetch(target_dir: Path) -> int:
    """Fetch arXiv metadata and download new PDFs into target directory."""
    try:
        fetcher = ArxivFetcher()
        paper_records = fetcher.fetch_metadata()
        if not paper_records:
            logger.info("No arXiv records fetched. Skip downloading.")
            return 0

        downloaded_count = fetcher.download_pdfs(
            paper_records=paper_records,
            target_dir=target_dir,
        )
        logger.info("Arxiv fetch completed. new_pdfs_count=%s", downloaded_count)
        return downloaded_count
    except Exception as exc:
        logger.error("Arxiv fetch workflow failed: %s", exc, exc_info=True)
        return 0
