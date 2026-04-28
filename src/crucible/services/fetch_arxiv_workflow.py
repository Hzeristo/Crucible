"""Fetch arXiv PDFs into a target directory."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.crucible.core.config import ChimeraConfig, get_config
from src.crucible.ports.arxiv.arxiv_fetch import ArxivFetcher
from src.crucible.services.task_service import TaskService

logger = logging.getLogger(__name__)


def run_arxiv_fetch(target_dir: Path, settings: ChimeraConfig | None = None) -> int:
    try:
        fetcher = ArxivFetcher(settings=settings or get_config())
        paper_records = fetcher.fetch_metadata()
        if not paper_records:
            logger.info("[Service] No arXiv records fetched. Skip downloading.")
            return 0

        downloaded_count = fetcher.download_pdfs(
            paper_records=paper_records,
            target_dir=target_dir,
        )
        logger.info("[Service] Arxiv fetch completed. new_pdfs_count=%s", downloaded_count)
        return downloaded_count
    except Exception as exc:
        logger.error("[Service] Arxiv fetch workflow failed: %s", exc, exc_info=True)
        return 0


def _papers_to_markdown(query: str, records: list[dict]) -> str:
    lines: list[str] = [
        f"# arXiv search: {query}",
        "",
        f"**Papers:** {len(records)}",
        "",
    ]
    for i, rec in enumerate(records, 1):
        pid = rec.get("id", "")
        title = rec.get("title", "")
        pdf_url = rec.get("pdf_url", "")
        summary = (rec.get("summary") or "").strip()
        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(f"- **arXiv:** `{pid}`")
        lines.append(f"- **PDF:** {pdf_url}")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).strip()


def _fetch_and_process_arxiv_sync(
    query: str,
    max_results: int,
    task_id: str | None = None,
    task_service: TaskService | None = None,
) -> str:
    def report(p: float, message: str | None = None) -> None:
        if task_id is not None and task_service is not None:
            task_service.update_progress(task_id, p, message)

    report(0.08, "Searching arXiv...")
    fetcher = ArxivFetcher(settings=get_config())
    records = fetcher.fetch_search_results(query, max_results)
    if not records:
        report(0.95, "No papers returned; finishing.")
        return f"# arXiv search: {query}\n\nNo papers returned (check query or network)."

    report(0.22, f"Found {len(records)} paper(s). Preparing content...")
    nrec = max(len(records), 1)
    for i, rec in enumerate(records):
        p = 0.22 + (i + 1) / nrec * 0.58
        title = (rec.get("title") or "Untitled")[:60]
        report(p, f"Processing {i + 1}/{len(records)}: {title}...")

    report(0.85, "Building Markdown output...")
    return _papers_to_markdown(query, records)


async def fetch_and_process_arxiv(
    query: str,
    max_results: int = 5,
    *,
    task_id: str | None = None,
    task_service: TaskService | None = None,
) -> str:
    """
    Search arXiv and format hits as a single Markdown string (metadata + abstract).

    Blocking HTTP/XML runs in a thread pool to avoid stalling the event loop.
    When ``task_id`` and ``task_service`` are set, progress is written for polling.
    """
    q = (query or "").strip()
    n = int(max_results) if max_results is not None else 5
    return await asyncio.to_thread(
        _fetch_and_process_arxiv_sync, q, n, task_id, task_service
    )
