"""Thin CLI entrypoint for batch markdown filtering workflow."""

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

from src.crucible.core.config import get_config  # noqa: E402
from src.crucible.services.batch_filter_workflow import run_batch_filter  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run batch filter on markdown papers.")
    parser.add_argument(
        "--md-papers-dir",
        type=Path,
        default=None,
        help="Optional markdown source directory.",
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
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    try:
        settings = get_config()
        settings.ensure_directories()
        stats = run_batch_filter(md_papers_dir=args.md_papers_dir, settings=settings)
        print("Batch filter completed.")
        print(f"Source: {stats.source_dir or 'N/A'}")
        print(f"Total: {stats.total}")
        print(f"Must Read: {stats.must_read}")
        print(f"Skim: {stats.skim}")
        print(f"Reject: {stats.reject}")
        print(f"Errors: {stats.errors}")
        print(f"Must Read Titles: {stats.must_read_titles}")
        return 0 if stats.errors == 0 else 1
    except Exception:
        logging.getLogger(__name__).exception("run_batch_filter script failed.")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
