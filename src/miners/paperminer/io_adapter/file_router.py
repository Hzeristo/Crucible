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
        """Resolve archive root from PaperMiner routed settings."""
        return self.settings.paper_miner_or_default.filtered_dir

    def _resolve_failed_dir(self) -> Path:
        """Resolve failed artifact quarantine directory from settings."""
        return self.settings.paper_miner_or_default.failed_dir

    def _resolve_md_papers_raw_dir(self) -> Path:
        """Resolve raw markdown output root with config-first fallback."""
        return self.settings.paper_miner_or_default.md_papers_raw_dir

    def _resolve_arxivpdf_dir(self) -> Path:
        """Resolve source PDF root with config-first fallback."""
        return self.settings.paper_miner_or_default.arxivpdf_dir

    def relocate_pdf_for_deep_read(self, source_pdf: Path, fancy_basename: str) -> None:
        if not source_pdf.is_file():
            raise RuntimeError(f"Deep-read source PDF not found: {source_pdf}")
        target_path = self.settings.vault_assets_dir / f"{fancy_basename}.pdf"
        try:
            self.settings.vault_assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_pdf, target_path)
        except OSError as exc:
            raise RuntimeError(
                f"Deep-read PDF copy failed from {source_pdf} to {target_path}: {exc}"
            ) from exc

    def route_and_cleanup(
        self,
        paper: Paper,
        analysis_or_verdict: PaperAnalysisResult | VerdictDecision,
    ) -> None:
        """
        Move markdown to verdict archive and remove stale source artifacts.

        File operations are strict: non-trivial filesystem failures raise RuntimeError.
        """
        if isinstance(analysis_or_verdict, PaperAnalysisResult):
            verdict = analysis_or_verdict.verdict
            score = analysis_or_verdict.score
            reason = analysis_or_verdict.novelty_delta
        else:
            verdict = analysis_or_verdict
            score = paper.metadata.score or ""
            reason = paper.metadata.reason or ""

        filtered_dir = self._resolve_filtered_dir()
        verdict_dir = filtered_dir / verdict.value.replace(" ", "_")
        try:
            verdict_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Failed creating verdict archive dir {verdict_dir} for paper_id={paper.id}: {exc}"
            ) from exc

        analysis = (
            analysis_or_verdict
            if isinstance(analysis_or_verdict, PaperAnalysisResult)
            else None
        )
        fancy_basename = compute_fancy_basename(paper, analysis)

        md_path = paper.content_path
        md_target = verdict_dir / f"{fancy_basename}.md"
        self._move_file_to_target(md_path, md_target, "archive markdown", paper.id)

        raw_dir = self._resolve_md_papers_raw_dir() / paper.id
        self._remove_dir_if_exists(raw_dir, "cleanup raw folder", paper.id)

        pdf_source = self._resolve_arxivpdf_dir() / f"{paper.id}.pdf"
        if verdict in (VerdictDecision.MUST_READ, VerdictDecision.SKIM):
            self._promote_pdf_to_vault(pdf_source, f"{fancy_basename}.pdf", paper.id)
        else:
            self._remove_file_if_exists(pdf_source, "delete rejected pdf", paper.id)

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
        except OSError as exc:
            raise RuntimeError(
                f"Failed appending audit log for paper_id={paper.id} at {self.audit_log_path}: {exc}"
            ) from exc

    def route_failed_cleanup(
        self,
        *,
        paper_id: str,
        md_path: Path | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        """
        Force-degrade failed paper artifacts out of hot zones.

        - Markdown -> papers/failed
        - PDF -> papers/failed
        - raw_dir -> strict remove
        """
        failed_dir = self._resolve_failed_dir()
        try:
            failed_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Failed creating failed quarantine dir {failed_dir} for paper_id={paper_id}: {exc}"
            ) from exc

        md_source = md_path
        if md_source is None:
            md_root = self.settings.paper_miner_or_default.md_papers_dir
            md_source = md_root / f"{paper_id}.md"
        pdf_source = self._resolve_arxivpdf_dir() / f"{paper_id}.pdf"
        failed_md_target = failed_dir / f"{paper_id}.md"
        failed_pdf_target = failed_dir / f"{paper_id}.pdf"

        self._move_file_to_target(md_source, failed_md_target, "failed markdown", paper_id)
        self._move_file_to_target(pdf_source, failed_pdf_target, "failed pdf", paper_id)

        raw_target = raw_dir or (self._resolve_md_papers_raw_dir() / paper_id)
        self._remove_dir_if_exists(raw_target, "failed raw cleanup", paper_id)

    def _move_file_to_target(
        self,
        source: Path,
        target: Path,
        operation: str,
        paper_id: str,
    ) -> None:
        """Strict move with overwrite fallback; raises RuntimeError on OSError."""
        try:
            if not source.exists() or not source.is_file():
                logger.info(
                    "No source file to %s. paper_id=%s path=%s",
                    operation,
                    paper_id,
                    source,
                )
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RuntimeError(
                        f"Failed removing existing target before {operation}. "
                        f"paper_id={paper_id} target={target}: {exc}"
                    ) from exc
            moved = Path(shutil.move(str(source), str(target)))
            logger.info(
                "Moved file for %s. paper_id=%s from=%s to=%s",
                operation,
                paper_id,
                source,
                moved,
            )
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError(
                f"Failed to {operation}. paper_id={paper_id} source={source} target={target}: {exc}"
            ) from exc

    def _remove_file_if_exists(self, path: Path, operation: str, paper_id: str) -> None:
        """Strict file remove; ignore only file-not-found races."""
        try:
            if not path.exists():
                return
            path.unlink()
            logger.info("Removed file for %s. paper_id=%s path=%s", operation, paper_id, path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(
                f"Failed to {operation}. paper_id={paper_id} path={path}: {exc}"
            ) from exc

    def _remove_dir_if_exists(self, path: Path, operation: str, paper_id: str) -> None:
        """Strict directory remove; ignore only file-not-found races."""
        try:
            if not path.exists():
                return
            shutil.rmtree(path)
            logger.info("Removed dir for %s. paper_id=%s path=%s", operation, paper_id, path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(
                f"Failed to {operation}. paper_id={paper_id} path={path}: {exc}"
            ) from exc

    def _promote_pdf_to_vault(self, source_pdf: Path, target_name: str, paper_id: str) -> None:
        """Move/copy source PDF into vault assets and clear staging PDF."""
        vault_assets_dir = self.settings.vault_assets_dir
        if vault_assets_dir is None:
            vault_assets_dir = self.settings.vault_root / "02_Assets" / "Papers"
        target_pdf = vault_assets_dir / target_name

        if not source_pdf.exists() or not source_pdf.is_file():
            logger.info("No source pdf to promote. paper_id=%s path=%s", paper_id, source_pdf)
            return

        try:
            vault_assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_pdf, target_pdf)
            source_pdf.unlink()
            logger.info(
                "Promoted PDF to vault assets and removed staging copy. paper_id=%s from=%s to=%s",
                paper_id,
                source_pdf,
                target_pdf,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Failed promoting PDF to vault. paper_id={paper_id} source={source_pdf} target={target_pdf}: {exc}"
            ) from exc

    def cleanup_playground(self, raw_dir: Path, md_file: Path | None = None) -> None:
        """Cleanup one playground raw folder and optional clean markdown file."""
        self._remove_dir_if_exists(raw_dir, "cleanup playground raw dir", "playground")
        # if md_file is not None:
        #     self._remove_file_if_exists(md_file, "cleanup playground md", "playground")
