"""Thin CLI entrypoint for PDF ingestion workflow."""

# 使用示例:
#   python scripts/run_ingest.py
#   python scripts/run_ingest.py --input-dir papers/arxivpdf --output-dir papers/md_papers_raw --clean-dir papers/md_papers
#   python scripts/run_ingest.py --input-dir papers/otherpdf --output-dir papers/md_papers_raw --clean-dir papers/md_others
#   python scripts/run_ingest.py -l DEBUG

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    """Return repository root based on this script location."""
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import Settings  # noqa: E402
from src.miners.paperminer.workflows.ingest_pdfs import run_pdf_ingestion  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Build parser for PDF ingestion CLI."""
    settings = Settings()
    pm = settings.paper_miner_or_default
    default_input = pm.arxivpdf_dir or (
        settings.project_root / "papers" / "arxivpdf"
    )
    default_raw_output = pm.md_papers_raw_dir or (
        settings.project_root / "papers" / "md_papers_raw"
    )
    default_clean_output = pm.md_papers_dir or (
        settings.project_root / "papers" / "md_papers"
    )

    parser = argparse.ArgumentParser(
        description="Run MinerU ingestion for all PDFs in a directory."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input,
        help="Directory containing source PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_raw_output,
        help="Directory where MinerU raw markdown folders are generated.",
    )
    parser.add_argument(
        "--clean-dir",
        type=Path,
        default=default_clean_output,
        help="Directory where cleaned markdown files are extracted.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level.",
    )
    return parser


def configure_logging(level: str) -> None:
    """Configure root logging for this script."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    """Parse CLI args, execute PDF ingestion, and print summary."""
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    try:
        success_count = run_pdf_ingestion(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            clean_dir=args.clean_dir,
        )
        print("PDF ingestion completed.")
        print(f"Input dir: {Path(args.input_dir)}")
        print(f"Raw output dir: {Path(args.output_dir)}")
        print(f"Clean output dir: {Path(args.clean_dir)}")
        print(f"Success count: {success_count}")
        return 0
    except FileNotFoundError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 2
    except Exception:
        logging.getLogger(__name__).exception("run_ingest script failed.")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
