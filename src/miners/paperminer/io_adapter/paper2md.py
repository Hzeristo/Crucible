"""IO adapter for converting PDF files to markdown via MinerU."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


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
        """检测可用的 MinerU 可执行命令。"""
        if shutil.which("mineru"):
            return "mineru"
        raise EnvironmentError("MinerU is not installed or not in PATH.")

    def convert(self, pdf_path: Path) -> Path:
        """
        转换 PDF 并返回生成的 Markdown 文件路径。
        注意：MinerU 会在 output_root 下生成一个以 pdf_path.stem 命名的子文件夹。
        """
        if not pdf_path.is_absolute():
            raise ValueError(f"pdf_path MUST be an absolute path. Got: {pdf_path}")
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a .pdf file, got: {pdf_path.name}")

        # 1. 检查是否已经存在
        folder_name = pdf_path.stem
        target_dir = self.output_root / folder_name
        target_md = target_dir / f"{folder_name}.md"

        if target_md.exists():
            logger.info(f"⏭️ Skipping conversion, MD exists: {target_md}")
            return target_md

        # 2. 调用 MinerU
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
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(f"MinerU timed out for {pdf_path.name}")
            raise RuntimeError(f"Conversion timed out for {pdf_path.name}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            logger.error(f"MinerU failed for {pdf_path.name}: {stderr}")
            raise RuntimeError(f"Conversion failed for {pdf_path.name}") from exc
        except OSError as exc:
            logger.error(f"Failed to execute MinerU command '{self.cmd}': {exc}")
            raise RuntimeError("Failed to execute MinerU command.") from exc

        # 3. 验证产物
        if not target_md.exists():
            # 有时候 mineru 生成的文件名可能跟 stem 不完全一致，这里需要鲁棒性处理
            # 比如找该目录下唯一的 .md 文件
            mds = sorted(target_dir.rglob("*.md"))
            if len(mds) == 1:
                return mds[0]
            if len(mds) > 1:
                logger.warning(
                    f"Multiple markdown files found in {target_dir}, using {mds[0].name}"
                )
                return mds[0]
            raise FileNotFoundError(
                f"Conversion reported success but no MD found in {target_dir}"
            )

        return target_md
