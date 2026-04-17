"""Daily Chimera pipeline: fetch → ingest → triage → Telegram."""

from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import quote

from src.crucible.core.config import Settings
from src.crucible.core.schemas import BatchFilterStats
from src.crucible.ports.notify.telegram_notifier import TelegramNotifier
from src.crucible.services.batch_filter_workflow import run_batch_filter
from src.crucible.services.fetch_arxiv_workflow import run_arxiv_fetch
from src.crucible.ports.ingest.mineru_pipeline import run_pdf_ingestion

logger = logging.getLogger(__name__)


def run_daily_pipeline(settings: Settings | None = None) -> None:
    if settings is None:
        settings = Settings()
    logger.info("=== Chimera Daily Pipeline Started ===")

    pm = settings.paper_miner_or_default
    input_dir = pm.arxivpdf_dir or (settings.project_root / "papers" / "arxivpdf")
    new_pdfs_count = run_arxiv_fetch(target_dir=input_dir)
    logger.info("Arxiv fetching completed. new_pdfs_count=%s", new_pdfs_count)

    raw_output_dir = pm.md_papers_raw_dir or (
        settings.project_root / "papers" / "md_papers_raw"
    )
    clean_dir = pm.md_papers_dir or (
        settings.project_root / "papers" / "md_papers"
    )
    ingested_count = run_pdf_ingestion(
        input_dir=input_dir,
        output_dir=raw_output_dir,
        clean_dir=clean_dir,
        settings=settings,
    )
    logger.info("Ingestion completed. success_count=%s", ingested_count)

    stats = run_batch_filter(md_papers_dir=clean_dir, settings=settings)
    logger.info("Triage completed. stats=%s", stats)

    report_message, reply_markup = _render_daily_report(
        stats=stats, new_pdfs_count=new_pdfs_count
    )
    notifier = TelegramNotifier(settings=settings)
    notifier.send_summary(html_message=report_message, reply_markup=reply_markup)


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
