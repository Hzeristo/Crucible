"""IO adapter for routing processed papers to archive locations."""

from __future__ import annotations

import csv
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.crucible.core.config import Settings, load_config
from src.crucible.utils.filename import compute_fancy_basename

from ..core.paper import Paper
from ..core.verdict import PaperAnalysisResult, VerdictDecision

logger = logging.getLogger(__name__)


class PaperRouter:
    """Move processed paper artifacts based on verdict and clean leftovers."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_config()
        self.project_root = self.settings.project_root
        self.audit_log_path = self.project_root / "papers" / "audit_log.csv"
        self._ensure_audit_log_file()

    def _ensure_audit_log_file(self) -> None:
        """Create audit CSV with header when file does not exist."""
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.audit_log_path.exists():
                return
            with self.audit_log_path.open("a", encoding="utf-8", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    ["timestamp", "paper_id", "title", "verdict", "score", "reason"]
                )
            logger.info("Initialized audit log file: %s", self.audit_log_path)
        except OSError as exc:
            logger.warning(
                "Failed to initialize audit log file: %s error=%s",
                self.audit_log_path,
                exc,
            )

    def _resolve_filtered_dir(self) -> Path:
        """Resolve archive root, defaulting to project-root papers/filtered."""
        pm = self.settings.paper_miner_or_default
        filtered_dir = pm.filtered_dir
        if filtered_dir is None:
            filtered_dir = self.project_root / "papers" / "filtered"
        filtered_dir.mkdir(parents=True, exist_ok=True)
        return filtered_dir

    def _resolve_md_papers_raw_dir(self) -> Path:
        """Resolve raw markdown output root with config-first fallback."""
        pm = self.settings.paper_miner_or_default
        if pm.md_papers_raw_dir is not None:
            return pm.md_papers_raw_dir
        return self.project_root / "papers" / "md_papers_raw"

    def _resolve_arxivpdf_dir(self) -> Path:
        """Resolve source PDF root with config-first fallback."""
        pm = self.settings.paper_miner_or_default
        if pm.arxivpdf_dir is not None:
            return pm.arxivpdf_dir
        return self.project_root / "papers" / "arxivpdf"

    def route_and_cleanup(
        self,
        paper: Paper,
        analysis_or_verdict: PaperAnalysisResult | VerdictDecision,
    ) -> None:
        """
        Move markdown to verdict archive and remove stale source artifacts.

        This method is intentionally best-effort: all failures are logged as
        warnings and never raised, so workflow orchestration is not interrupted.
        """
        if isinstance(analysis_or_verdict, PaperAnalysisResult):
            verdict = analysis_or_verdict.verdict
            score = analysis_or_verdict.score
            reason = analysis_or_verdict.novelty_delta
        else:
            verdict = analysis_or_verdict
            score = paper.metadata.get("score", "")
            reason = paper.metadata.get("reason", "")

        filtered_dir = self._resolve_filtered_dir()
        verdict_dir = filtered_dir / verdict.value.replace(" ", "_")
        verdict_dir.mkdir(parents=True, exist_ok=True)

        analysis = (
            analysis_or_verdict
            if isinstance(analysis_or_verdict, PaperAnalysisResult)
            else None
        )
        fancy_basename = compute_fancy_basename(paper, analysis)

        md_path = Path(paper.content_path)
        md_target = verdict_dir / f"{fancy_basename}.md"
        try:
            moved_md = Path(shutil.move(str(md_path), str(md_target)))
            logger.info("Moved markdown to archive: %s -> %s", md_path, moved_md)
        except FileNotFoundError:
            logger.warning(
                "Markdown file missing, skip routing. paper_id=%s path=%s",
                paper.id,
                md_path,
            )
        except OSError as exc:
            logger.warning(
                "Failed to move markdown file, skip routing. paper_id=%s path=%s error=%s",
                paper.id,
                md_path,
                exc,
            )

        raw_dir = self._resolve_md_papers_raw_dir() / paper.id
        try:
            if raw_dir.exists() and raw_dir.is_dir():
                shutil.rmtree(raw_dir)
                logger.info("Removed stale raw folder: %s", raw_dir)
            else:
                logger.info("No stale raw folder to remove: %s", raw_dir)
        except FileNotFoundError:
            logger.warning("Raw folder already absent during cleanup: %s", raw_dir)
        except OSError as exc:
            logger.warning("Failed to remove raw folder: %s error=%s", raw_dir, exc)

        pdf_source = self._resolve_arxivpdf_dir() / f"{paper.id}.pdf"
        pdf_target = verdict_dir / f"{fancy_basename}.pdf"
        try:
            if pdf_source.exists():
                moved_pdf = Path(shutil.move(str(pdf_source), str(pdf_target)))
                logger.info("Archived source pdf: %s -> %s", pdf_source, moved_pdf)
            else:
                logger.info("No source pdf to archive: %s", pdf_source)
        except FileNotFoundError:
            logger.warning("Source pdf disappeared during archive: %s", pdf_source)
        except OSError as exc:
            logger.warning("Failed to archive source pdf: %s error=%s", pdf_source, exc)

        try:
            with self.audit_log_path.open("a", encoding="utf-8", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        datetime.now().isoformat(timespec="seconds"),
                        paper.id,
                        paper.title,
                        verdict.value,
                        score,
                        reason,
                    ]
                )
            logger.info("Appended audit log record for paper_id=%s", paper.id)
        except FileNotFoundError:
            logger.warning(
                "Audit log path missing during write: %s", self.audit_log_path
            )
        except OSError as exc:
            logger.warning(
                "Failed to append audit log for paper_id=%s error=%s", paper.id, exc
            )
