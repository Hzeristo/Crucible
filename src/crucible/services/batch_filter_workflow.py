"""Batch markdown filtering workflow."""

from __future__ import annotations

import logging
from pathlib import Path

from src.crucible.bootstrap import build_openai_client
from src.crucible.core.config import ChimeraConfig, get_config
from src.crucible.core.schemas import (
    BatchFilterStats,
    BatchMustReadItem,
    VerdictDecision,
)
from src.crucible.ports.papers.paper_archive_adapter import PaperArchiveAdapter
from src.crucible.ports.papers.paper_loader import PaperLoader
from src.crucible.ports.prompts.jinja_prompt_manager import PromptManager
from src.crucible.ports.vault.vault_note_writer import VaultNoteWriter
from src.crucible.services.filter_service import FilterService

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover

    def tqdm(iterable, **_kwargs):  # type: ignore[no-redef]
        return iterable


logger = logging.getLogger(__name__)


def _resolve_md_papers_dir(settings: ChimeraConfig, md_papers_dir: Path | None) -> Path:
    if md_papers_dir is not None:
        candidate = md_papers_dir.expanduser()
        if not candidate.is_absolute():
            return (settings.project_root / candidate).resolve()
        return candidate.resolve()

    return settings.paper_miner_or_default.md_papers_dir


def run_batch_filter(
    md_papers_dir: Path | None = None,
    *,
    settings: ChimeraConfig | None = None,
) -> BatchFilterStats:
    if settings is None:
        settings = get_config()
    settings.ensure_directories()
    loader = PaperLoader()
    prompt_manager = PromptManager()
    llm = build_openai_client(settings)
    engine = FilterService(
        llm_client=llm,
        prompt_manager=prompt_manager,
    )
    writer = VaultNoteWriter(settings=settings, prompt_manager=prompt_manager)
    router = PaperArchiveAdapter(settings=settings)

    source_dir = _resolve_md_papers_dir(settings=settings, md_papers_dir=md_papers_dir)
    stats = BatchFilterStats(source_dir=source_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        logger.warning("[Service] Markdown papers directory does not exist: %s", source_dir)
        return stats

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        logger.info("[Service] No markdown papers found in %s", source_dir)
        return stats

    stats.total = len(md_files)
    total = len(md_files)
    progress = tqdm(md_files, total=total, unit="paper")

    for idx, md_file in enumerate(progress, start=1):
        paper_id_for_cleanup = md_file.stem
        progress.set_description(f"[{idx}/{total}] Analyzing {md_file.name}")
        try:
            paper = loader.load_paper(md_file)
            stats.processed_ids.append(paper.id)
            paper_id_for_cleanup = paper.id
            result = engine.evaluate_paper(paper)

            if result.verdict == VerdictDecision.MUST_READ:
                stats.must_read += 1
                moniker = result.short_moniker.strip()
                display_title = (
                    f"{paper.id} {moniker}".strip() if moniker else str(paper.id)
                )
                output_path = writer.write_knowledge_node(paper, result)
                stats.must_read_titles.append(display_title)
                stats.must_read_items.append(
                    BatchMustReadItem(
                        score=int(result.score),
                        id=paper.id,
                        paper_id=paper.id,
                        short_moniker=moniker,
                        filename=output_path.name,
                        title=display_title,
                        novelty=result.novelty_delta,
                    )
                )
            elif result.verdict == VerdictDecision.SKIM:
                stats.skim += 1
                writer.write_knowledge_node(paper, result)
            else:
                stats.reject += 1
            router.route_and_cleanup(paper, result)
        except Exception as exc:
            stats.errors += 1
            logger.error("[Service] Failed processing %s: %s", paper_id_for_cleanup, exc)
            try:
                router.route_failed_cleanup(
                    paper_id=paper_id_for_cleanup,
                    md_path=md_file,
                )
            except RuntimeError as cleanup_exc:
                logger.warning(
                    "[Service] Failed cleanup fallback for %s: %s",
                    paper_id_for_cleanup,
                    cleanup_exc,
                )

    return stats
