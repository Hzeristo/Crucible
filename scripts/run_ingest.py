"""Thin CLI entrypoint for PDF ingestion workflow."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.crucible.core.config import Settings, load_config  # noqa: E402
from src.crucible.ports.ingest.mineru_pipeline import run_pdf_ingestion  # noqa: E402


def build_parser(
    default_raw_output: Path,
    default_clean_output: Path,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run MinerU ingestion for all PDFs in a directory."
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing source PDFs.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=default_clean_output,
        help="Directory where cleaned markdown files are extracted.",
    )
    parser.set_defaults(raw_dir=default_raw_output)
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    settings = load_config()
    settings.ensure_directories()
    parser = build_parser(
        settings.playground_dir / "md_raw",
        settings.playground_dir / "md_clean",
    )
    args = parser.parse_args()
    configure_logging()

    try:
        success_count = run_pdf_ingestion(
            input_dir=args.input_dir,
            output_dir=args.raw_dir,
            clean_dir=args.out,
            settings=settings,
        )
        print("PDF ingestion completed.")
        print(f"Input dir: {Path(args.input_dir)}")
        print(f"Raw output dir: {Path(args.raw_dir)}")
        print(f"Clean output dir: {Path(args.out)}")
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
