"""Batch workflow: evaluate markdown papers, write accepted notes, and clean artifacts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.crucible.core.config import Settings
from src.crucible.llm_gateway.client import DeepSeekClient
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


def _resolve_md_papers_dir(settings: Settings, md_papers_dir: Path | None) -> Path:
    """Resolve markdown source dir with explicit argument > config > project fallback."""
    if md_papers_dir is not None:
        candidate = md_papers_dir.expanduser()
        if not candidate.is_absolute():
            return (settings.project_root / candidate).resolve()
        return candidate.resolve()

    pm = settings.paper_miner_or_default
    if pm.md_papers_dir is not None:
        return pm.md_papers_dir

    return (settings.project_root / "papers" / "md_papers").resolve()


def run_batch_filter(md_papers_dir: Path | None = None) -> dict[str, Any]:
    """Run full batch filtering and return processing stats for script layer consumption."""
    settings = Settings()
    loader = PaperLoader()
    prompt_manager = PromptManager()
    engine = PaperFilterEngine(
        llm_client=DeepSeekClient(),
        prompt_manager=prompt_manager,
    )
    writer = VaultWriter(settings=settings, prompt_manager=prompt_manager)
    router = PaperRouter(settings=settings)

    source_dir = _resolve_md_papers_dir(settings=settings, md_papers_dir=md_papers_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        logger.warning("Markdown papers directory does not exist: %s", source_dir)
        return {
            "total": 0,
            "must_read": 0,
            "skim": 0,
            "reject": 0,
            "errors": 0,
            "must_read_titles": [],
            "must_read_items": [],
            "source_dir": str(source_dir),
        }

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        logger.info("No markdown papers found in %s", source_dir)
        return {
            "total": 0,
            "must_read": 0,
            "skim": 0,
            "reject": 0,
            "errors": 0,
            "must_read_titles": [],
            "must_read_items": [],
            "source_dir": str(source_dir),
        }

    stats: dict[str, Any] = {
        "total": len(md_files),
        "must_read": 0,
        "skim": 0,
        "reject": 0,
        "errors": 0,
        "must_read_titles": [],
        "must_read_items": [],
        "source_dir": str(source_dir),
    }
    total = len(md_files)
    progress = tqdm(md_files, total=total, unit="paper")

    for idx, md_file in enumerate(progress, start=1):
        paper = None
        progress.set_description(f"[{idx}/{total}] Analyzing {md_file.name}")
        try:
            paper = loader.load_paper(md_file)
            result = engine.evaluate_paper(paper)

            if result.verdict == VerdictDecision.MUST_READ:
                stats["must_read"] += 1
                moniker = result.short_moniker.strip()
                display_title = (
                    f"{paper.id} {moniker}".strip() if moniker else str(paper.id)
                )
                output_path = writer.write_knowledge_node(paper, result)
                stats["must_read_titles"].append(display_title)
                stats["must_read_items"].append(
                    {
                        "score": int(result.score),
                        "id": paper.id,
                        "paper_id": paper.id,
                        "short_moniker": moniker,
                        "filename": output_path.name,
                        "title": display_title,
                        "novelty": result.novelty_delta,
                    }
                )
            elif result.verdict == VerdictDecision.SKIM:
                stats["skim"] += 1
                writer.write_knowledge_node(paper, result)
            else:
                stats["reject"] += 1

            # Always cleanup and archive after decision, regardless of verdict.
            router.route_and_cleanup(paper, result)
        except Exception:
            stats["errors"] += 1
            logger.exception("Batch processing failed for markdown file: %s", md_file)
            if paper is not None:
                logger.warning(
                    "Skip cleanup because no stable analysis result. paper_id=%s",
                    paper.id,
                )

    return stats
