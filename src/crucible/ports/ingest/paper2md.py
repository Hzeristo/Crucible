"""Convert PDF files to markdown via MinerU CLI."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _log_mineru_streams(
    stdout: str | None,
    stderr: str | None,
    *,
    pdf_name: str,
    reason: str,
) -> None:
    """失败或疑似成功却无产物时，输出子进程完整 stdout/stderr 便于排查。"""
    out = (stdout or "").strip() or "(empty)"
    err = (stderr or "").strip() or "(empty)"
    logger.error("[Ingest] MinerU %s | %s | stdout:\n%s", reason, pdf_name, out)
    logger.error("[Ingest] MinerU %s | %s | stderr:\n%s", reason, pdf_name, err)


class MineruNotInstalledError(Exception):
    """`mineru` 可执行文件不在 PATH 中（与 PDF 缺失等 OSError 区分开）。"""


class MineruClient:
    """MinerU 命令行适配器，负责将 PDF 转换为 Markdown。"""

    def __init__(self, output_root: Path) -> None:
        if not output_root.is_absolute():
            raise ValueError(
                f"output_root MUST be an absolute path. Got: {output_root}"
            )

        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.cmd = self._detect_command()

    def _detect_command(self) -> str:
        if shutil.which("mineru"):
            return "mineru"
        raise MineruNotInstalledError("MinerU is not installed or not in PATH.")

    def convert(self, pdf_path: Path) -> Path:
        if not pdf_path.is_absolute():
            raise ValueError(f"pdf_path MUST be an absolute path. Got: {pdf_path}")
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a .pdf file, got: {pdf_path.name}")

        folder_name = pdf_path.stem
        target_dir = self.output_root / folder_name
        target_md = target_dir / f"{folder_name}.md"

        if target_md.exists():
            logger.info("[Ingest] Skipping conversion, MD exists: %s", target_md)
            return target_md

        cmd = [
            self.cmd,
            "-p",
            str(pdf_path),
            "-o",
            str(self.output_root),
            "-m",
            "auto",
            "-d",
            "cuda",
        ]

        try:
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
            mineru_stdout, mineru_stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            logger.error("[Ingest] MinerU timed out for %s", pdf_path.name)
            _log_mineru_streams(
                getattr(exc, "output", None),
                getattr(exc, "stderr", None),
                pdf_name=pdf_path.name,
                reason="timeout",
            )
            raise RuntimeError(f"Conversion timed out for {pdf_path.name}") from exc
        except subprocess.CalledProcessError as exc:
            logger.error("[Ingest] MinerU non-zero exit for %s", pdf_path.name)
            _log_mineru_streams(
                exc.stdout,
                exc.stderr,
                pdf_name=pdf_path.name,
                reason="non-zero exit",
            )
            raise RuntimeError(f"Conversion failed for {pdf_path.name}") from exc
        except OSError as exc:
            logger.error("[Ingest] Failed to execute MinerU command '%s': %s", self.cmd, exc)
            raise RuntimeError("Failed to execute MinerU command.") from exc

        if not target_md.exists():
            mds = sorted(target_dir.rglob("*.md"))
            if len(mds) == 1:
                return mds[0]
            if len(mds) > 1:
                logger.warning(
                    "[Ingest] Multiple markdown files found in %s, using %s",
                    target_dir,
                    mds[0].name,
                )
                return mds[0]
            _log_mineru_streams(
                mineru_stdout,
                mineru_stderr,
                pdf_name=pdf_path.name,
                reason="exit 0 but no .md",
            )
            raise FileNotFoundError(
                f"Conversion reported success but no MD found in {target_dir}"
            )

        return target_md
