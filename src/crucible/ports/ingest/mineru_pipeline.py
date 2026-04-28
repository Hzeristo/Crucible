"""Batch PDF ingestion through MinerU."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from src.crucible.core.config import ChimeraConfig, get_config
from src.crucible.core.schemas import Paper
from src.crucible.ports.ingest.paper2md import MineruClient
from src.crucible.ports.papers.paper_loader import PaperLoader

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover

    def tqdm(iterable, **_kwargs):  # type: ignore[no-redef]
        return iterable


logger = logging.getLogger(__name__)


def _normalize_against_project(path: Path, settings: ChimeraConfig) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (settings.project_root / expanded).resolve()


def _normalize_pdf_file(pdf_path: Path, settings: ChimeraConfig) -> Path:
    expanded = pdf_path.expanduser()
    resolved = (
        expanded.resolve()
        if expanded.is_absolute()
        else (settings.project_root / expanded).resolve()
    )
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF not found: {resolved}")
    if resolved.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {resolved.name}")
    return resolved


def _execute_mineru_pipeline(pdf_path: Path, raw_dir: Path, clean_dir: Path) -> Path:
    client = MineruClient(output_root=raw_dir)
    paper_loader = PaperLoader()
    stem = pdf_path.stem
    raw_md = client.convert(pdf_path)

    raw_paper_dir = raw_dir / stem
    if not raw_paper_dir.exists() or not raw_paper_dir.is_dir():
        raw_paper_dir = raw_md.parent

    return paper_loader.extract_and_clean(
        raw_paper_dir=raw_paper_dir,
        clean_dir=clean_dir,
        paper_stem=stem,
    )


@dataclass(frozen=True, slots=True)
class SinglePdfIngestOutcome:
    paper: Paper
    clean_md_path: Path
    mineru_raw_dir: Path
    source_pdf_path: Path


def ingest_to_playground(
    pdf_path: Path,
    settings: ChimeraConfig,
    raw_output_root: Path | None = None,
) -> tuple[Path, Path, Path]:
    abs_pdf = _normalize_pdf_file(pdf_path, settings)
    playground_pdf_dir = _normalize_against_project(settings.playground_dir / "pdfs", settings)
    playground_pdf_dir.mkdir(parents=True, exist_ok=True)
    staged_pdf = (playground_pdf_dir / abs_pdf.name).resolve()
    if staged_pdf != abs_pdf:
        shutil.copy2(abs_pdf, staged_pdf)

    playground_root = settings.playground_dir.resolve()
    if raw_output_root is None:
        normalized_raw = _normalize_against_project(playground_root / "md_raw", settings)
    else:
        candidate = _normalize_against_project(raw_output_root, settings)
        if not candidate.is_relative_to(playground_root):
            raise ValueError(
                f"raw_output_root must stay under playground_dir for isolation: {playground_root}"
            )
        normalized_raw = candidate
    normalized_clean = _normalize_against_project(playground_root / "md_clean", settings)

    stem = staged_pdf.stem
    clean_md = _execute_mineru_pipeline(staged_pdf, normalized_raw, normalized_clean)
    canonical_raw = normalized_raw / stem
    mineru_raw = canonical_raw if canonical_raw.is_dir() else normalized_raw
    return (staged_pdf, mineru_raw.resolve(), clean_md.resolve())


def ingest_to_papers(
    pdf_path: Path,
    settings: ChimeraConfig,
    raw_output_root: Path | None = None,
) -> tuple[Path, Path, Path]:
    abs_pdf = _normalize_pdf_file(pdf_path, settings)
    pm = settings.paper_miner_or_default
    papers_root = pm.papers_root.resolve()

    arxiv_pdf_dir = _normalize_against_project(pm.arxivpdf_dir, settings)
    arxiv_pdf_dir.mkdir(parents=True, exist_ok=True)
    staged_pdf = (arxiv_pdf_dir / abs_pdf.name).resolve()
    if staged_pdf != abs_pdf:
        shutil.copy2(abs_pdf, staged_pdf)

    if raw_output_root is None:
        normalized_raw = _normalize_against_project(pm.md_papers_raw_dir, settings)
    else:
        candidate = _normalize_against_project(raw_output_root, settings)
        if not candidate.is_relative_to(papers_root):
            raise ValueError(
                f"raw_output_root must stay under papers_root for isolation: {papers_root}"
            )
        normalized_raw = candidate
    normalized_clean = _normalize_against_project(pm.md_papers_dir, settings)

    stem = staged_pdf.stem
    clean_md = _execute_mineru_pipeline(staged_pdf, normalized_raw, normalized_clean)
    canonical_raw = normalized_raw / stem
    mineru_raw = canonical_raw if canonical_raw.is_dir() else normalized_raw
    return (staged_pdf, mineru_raw.resolve(), clean_md.resolve())


def ingest_single_pdf_for_deep_read(
    pdf_path: Path,
    settings: ChimeraConfig,
) -> SinglePdfIngestOutcome:
    abs_pdf = _normalize_pdf_file(pdf_path, settings)
    raw_root = settings.playground_dir / "md_raw"
    normalized_raw = _normalize_against_project(raw_root, settings)
    clean_dir = settings.playground_dir / "md_clean"
    normalized_clean = _normalize_against_project(clean_dir, settings)

    stem = abs_pdf.stem
    clean_md = _execute_mineru_pipeline(abs_pdf, normalized_raw, normalized_clean)
    paper_loader = PaperLoader()
    paper = paper_loader.load_paper(clean_md)
    canonical_raw = normalized_raw / stem
    mineru_raw = canonical_raw if canonical_raw.is_dir() else normalized_raw

    return SinglePdfIngestOutcome(
        paper=paper,
        clean_md_path=clean_md.resolve(),
        mineru_raw_dir=mineru_raw.resolve(),
        source_pdf_path=abs_pdf,
    )


def run_pdf_ingestion(
    input_dir: Path,
    output_dir: Path,
    clean_dir: Path,
    *,
    settings: ChimeraConfig | None = None,
) -> int:
    if settings is None:
        settings = get_config()
    normalized_input = _normalize_against_project(input_dir, settings)
    normalized_raw_output = _normalize_against_project(output_dir, settings)
    normalized_clean_dir = _normalize_against_project(clean_dir, settings)

    if not normalized_input.exists() or not normalized_input.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {normalized_input}")

    pdf_files = sorted(normalized_input.glob("*.pdf"))
    logger.info("[Ingest] Found %s PDF files in %s", len(pdf_files), normalized_input)
    if not pdf_files:
        logger.info("[Ingest] No PDF files found in %s", normalized_input)
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

            canonical_raw_dir = normalized_raw_output / paper_stem
            cleanup_target = (
                canonical_raw_dir if canonical_raw_dir.exists() else raw_paper_dir
            )
            try:
                if cleanup_target.exists() and cleanup_target.is_dir():
                    shutil.rmtree(cleanup_target)
                    logger.info("[Ingest] Removed raw folder after cleaning: %s", cleanup_target)
            except Exception as cleanup_exc:
                logger.warning(
                    "[Ingest] Failed to cleanup raw folder for %s: %s",
                    pdf_path.name,
                    cleanup_exc,
                )

            success_count += 1
        except Exception as exc:
            logger.error("[Ingest] PDF ingestion failed for %s: %s", pdf_path, exc)
            continue

    return success_count
