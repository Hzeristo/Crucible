"""Daily Chimera pipeline: fetch → ingest → triage → Telegram."""

from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import quote

from src.crucible.core.config import ChimeraConfig, get_config, PaperMinerSettings
from src.crucible.core.schemas import BatchFilterStats
from src.crucible.ports.notify.telegram_notifier import TelegramNotifier
from src.crucible.services.batch_filter_workflow import run_batch_filter
from src.crucible.services.fetch_arxiv_workflow import run_arxiv_fetch
from src.crucible.ports.ingest.mineru_pipeline import run_pdf_ingestion
from src.crucible.services.task_service import TaskService

logger = logging.getLogger(__name__)


def _merge_arxiv_overrides(
    settings: ChimeraConfig,
    arxiv_query: str | None,
    arxiv_max_results: int | None,
) -> ChimeraConfig:
    """Return config with paper_miner arxiv_query / arxiv_max_results overridden when set."""
    if arxiv_query is None and arxiv_max_results is None:
        return settings
    pm: PaperMinerSettings = settings.paper_miner_or_default
    updates: dict[str, str | int] = {}
    if arxiv_query is not None:
        q = str(arxiv_query).strip()
        if q:
            updates["arxiv_query"] = q
    if arxiv_max_results is not None:
        try:
            n = int(arxiv_max_results)
            updates["arxiv_max_results"] = max(1, min(n, 2000))
        except (TypeError, ValueError):
            pass
    if not updates:
        return settings
    new_pm = pm.model_copy(update=updates)
    return settings.model_copy(update={"paper_miner": new_pm})


def _update_task_progress(
    task_service: TaskService | None,
    task_id: str | None,
    progress: float,
    message: str,
) -> None:
    if task_service is not None and task_id is not None:
        task_service.update_progress(task_id, progress, message)


def run_daily_pipeline(
    settings: ChimeraConfig | None = None,
    *,
    arxiv_query: str | None = None,
    arxiv_max_results: int | None = None,
    skip_telegram: bool = False,
    task_id: str | None = None,
    task_service: TaskService | None = None,
) -> str:
    """
    Full Chimera daily path: arXiv fetch → MinerU ingestion → batch LLM triage → optional Telegram.

    When ``task_id`` and ``task_service`` are set (e.g. Oligo background task), progress is reported
    in bands: 0.0–0.2 fetch, 0.2–0.6 ingest, 0.6–0.95 batch filter, 0.95–1.0 Telegram (if not skipped).

    Returns a short text summary for logs and task completion payloads.
    """
    if settings is None:
        settings = get_config()
    settings = _merge_arxiv_overrides(settings, arxiv_query, arxiv_max_results)

    logger.info("[Service] === Chimera Daily Pipeline Started ===")

    pm = settings.paper_miner_or_default
    input_dir = pm.arxivpdf_dir or (settings.project_root / "papers" / "arxivpdf")

    _update_task_progress(
        task_service, task_id, 0.05, "ArXiv fetch (metadata + PDF download)..."
    )
    new_pdfs_count = run_arxiv_fetch(target_dir=input_dir, settings=settings)
    logger.info("[Service] Arxiv fetching completed. new_pdfs_count=%s", new_pdfs_count)
    _update_task_progress(
        task_service,
        task_id,
        0.2,
        f"ArXiv fetch done (new PDFs: {new_pdfs_count}).",
    )

    raw_output_dir = pm.md_papers_raw_dir or (
        settings.project_root / "papers" / "md_papers_raw"
    )
    clean_dir = pm.md_papers_dir or (
        settings.project_root / "papers" / "md_papers"
    )
    _update_task_progress(
        task_service, task_id, 0.25, "PDF ingestion (MinerU) → markdown..."
    )
    ingested_count = run_pdf_ingestion(
        input_dir=input_dir,
        output_dir=raw_output_dir,
        clean_dir=clean_dir,
        settings=settings,
    )
    logger.info("[Service] Ingestion completed. success_count=%s", ingested_count)
    _update_task_progress(
        task_service,
        task_id,
        0.6,
        f"Ingestion done (success count: {ingested_count}).",
    )

    _update_task_progress(
        task_service, task_id, 0.65, "Batch filter (LLM triage)..."
    )
    stats = run_batch_filter(md_papers_dir=clean_dir, settings=settings)
    logger.info("[Service] Triage completed. stats=%s", stats)
    _update_task_progress(
        task_service,
        task_id,
        0.95,
        (
            f"Triage done: total={stats.total} must_read={stats.must_read} "
            f"skim={stats.skim} reject={stats.reject} errors={stats.errors}."
        ),
    )

    if not skip_telegram:
        _update_task_progress(
            task_service, task_id, 0.96, "Telegram morning broadcast..."
        )
        report_message, reply_markup = _render_daily_report(
            stats=stats, new_pdfs_count=new_pdfs_count
        )
        notifier = TelegramNotifier(settings=settings)
        notifier.send_summary(html_message=report_message, reply_markup=reply_markup)
        _update_task_progress(task_service, task_id, 0.99, "Telegram sent.")
    else:
        _update_task_progress(
            task_service, task_id, 0.99, "Telegram skipped (skip_telegram=True)."
        )

    summary = (
        f"Daily pipeline completed. new_pdfs={new_pdfs_count} ingested={ingested_count} "
        f"batch_total={stats.total} must_read={stats.must_read} skim={stats.skim} "
        f"reject={stats.reject} errors={stats.errors} telegram={'no' if skip_telegram else 'yes'}"
    )
    logger.info("[Service] %s", summary)
    return summary


def _render_daily_report(
    stats: BatchFilterStats,
    new_pdfs_count: int,
) -> tuple[str, dict[str, list[list[dict[str, str]]]] | None]:
    total = int(stats.total)
    must_read = int(stats.must_read)
    skim = int(stats.skim)
    reject = int(stats.reject)

    items_raw = stats.must_read_items
    must_read_items: list[dict[str, Any]] = []
    inline_keyboard: list[list[dict[str, str]]] = []
    for item in items_raw:
        score = item.score
        paper_id = str(item.id).strip()
        filename = str(item.filename).strip()
        short_moniker = str(item.short_moniker).strip()
        legacy_title = str(item.title).strip()
        if short_moniker:
            title = f"{paper_id} {short_moniker}".strip() if paper_id else short_moniker
        elif legacy_title:
            title = legacy_title
        else:
            title = paper_id
        novelty = item.novelty
        encoded_id = quote(paper_id, safe="")
        arxiv_url = f"https://arxiv.org/abs/{encoded_id}" if paper_id else "#"
        obsidian_url = (
            f"https://chimeravaultrouter.haydenshui.workers.dev/?id={encoded_id}"
            if paper_id
            else "#"
        )
        short_for_button_paper = f"Paper {encoded_id}"
        short_for_button_obsidian = (
            f"Node for {short_moniker}" if short_moniker else f"Node for {encoded_id}"
        )
        inline_keyboard.append(
            [
                {"text": f"🌐 {short_for_button_paper}", "url": arxiv_url},
                {"text": f"🧠 {short_for_button_obsidian}", "url": obsidian_url},
            ]
        )
        must_read_items.append(
            {
                "score": int(score),
                "id": paper_id,
                "filename": filename,
                "title": html.escape(str(title), quote=False),
                "novelty": html.escape(str(novelty), quote=False),
            }
        )

    if not must_read_items:
        for title in stats.must_read_titles:
            must_read_items.append(
                {
                    "score": 0,
                    "id": "",
                    "filename": "",
                    "title": html.escape(str(title), quote=False),
                    "novelty": "N/A",
                }
            )

    lines: list[str] = [
        "🚨 <b>[BB Channel] Chimera Morning Broadcast</b> 🚨",
        "━━━━━━━━━━━━━━━━━━━━",
        '"Good morning, Senpai~ ♡ Here is the academic trash I\'ve digested for you."',
        "",
        f"📥 New PDFs fetched: <b>{int(new_pdfs_count)}</b>",
        f"📄 Ingested papers: <b>{total}</b>",
        f"💎 Must Read: <b>{must_read}</b>",
        f"🪶 Skim: <b>{skim}</b>",
        f"🗑️ Reject: {reject}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 <b>SURVIVING TARGETS (Please consume)</b>",
        "",
    ]
    if must_read_items:
        for item in must_read_items:
            lines.append(
                f"🔹 <b>[{item['score']}/10]</b> <code>{item['title']}</code>"
            )
            lines.append(f"   <i>💡 {item['novelty']}</i>")
    else:
        lines.append("<i>☕ All targets were garbage today. You can go back to sleep.</i>")

    html_message = "\n".join(lines).strip()
    reply_markup = {"inline_keyboard": inline_keyboard} if inline_keyboard else None
    return html_message, reply_markup
