"""Batch workflow: evaluate markdown papers, write accepted notes, and clean artifacts."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field
from src.crucible.core.config import Settings, load_config
from src.crucible.llm_gateway.client import OpenAICompatibleClient
from src.crucible.llm_gateway.prompt_manager import PromptManager

from ..core.verdict import VerdictDecision
from ..decision.filter_engine import PaperFilterEngine
from ..io_adapter.file_router import PaperRouter
from ..io_adapter.paper_loader import PaperLoader
from ..io_adapter.vault_writer import VaultWriter

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - graceful fallback when tqdm is missing.

    def tqdm(iterable, **_kwargs):  # type: ignore[no-redef]
        return iterable


logger = logging.getLogger(__name__)


class BatchMustReadItem(BaseModel):
    score: int
    id: str
    paper_id: str
    short_moniker: str
    filename: str
    title: str
    novelty: str


class BatchFilterStats(BaseModel):
    total: int = 0
    must_read: int = 0
    skim: int = 0
    reject: int = 0
    errors: int = 0
    processed_ids: list[str] = Field(default_factory=list)
    must_read_titles: list[str] = Field(default_factory=list)
    must_read_items: list[BatchMustReadItem] = Field(default_factory=list)
    source_dir: Path | None = None


def _resolve_md_papers_dir(settings: Settings, md_papers_dir: Path | None) -> Path:
    """Resolve markdown source dir with explicit argument > config > project fallback."""
    if md_papers_dir is not None:
        candidate = md_papers_dir.expanduser()
        if not candidate.is_absolute():
            return (settings.project_root / candidate).resolve()
        return candidate.resolve()

    return settings.paper_miner_or_default.md_papers_dir


def run_batch_filter(md_papers_dir: Path | None = None) -> BatchFilterStats:
    """Run full batch filtering and return processing stats for script layer consumption."""
    settings = load_config()
    settings.ensure_directories()
    loader = PaperLoader()
    prompt_manager = PromptManager()
    engine = PaperFilterEngine(
        llm_client=OpenAICompatibleClient(
            api_key=settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
            base_url=settings.default_llm_base_url,
            model=settings.default_llm_model,
        ),
        prompt_manager=prompt_manager,
    )
    writer = VaultWriter(settings=settings, prompt_manager=prompt_manager)
    router = PaperRouter(settings=settings)

    source_dir = _resolve_md_papers_dir(settings=settings, md_papers_dir=md_papers_dir)
    stats = BatchFilterStats(source_dir=source_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        logger.warning("Markdown papers directory does not exist: %s", source_dir)
        return stats

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        logger.info("No markdown papers found in %s", source_dir)
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
            logger.error("Failed processing %s: %s", paper_id_for_cleanup, exc)
            try:
                router.route_failed_cleanup(
                    paper_id=paper_id_for_cleanup,
                    md_path=md_file,
                )
            except RuntimeError as cleanup_exc:
                logger.warning(
                    "Failed cleanup fallback for %s: %s",
                    paper_id_for_cleanup,
                    cleanup_exc,
                )

    return stats
