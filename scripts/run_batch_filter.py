"""Thin CLI entrypoint for batch markdown filtering workflow."""

# 使用示例:
#   python scripts/run_batch_filter.py
#   python scripts/run_batch_filter.py --md-papers-dir papers/md_papers
#   python scripts/run_batch_filter.py -l DEBUG

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

from src.miners.paperminer.workflows.batch_filter import run_batch_filter  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Build parser for batch filter CLI."""
    parser = argparse.ArgumentParser(description="Run batch filter on markdown papers.")
    parser.add_argument(
        "--md-papers-dir",
        type=Path,
        default=None,
        help="Optional markdown source directory. Defaults to config.md_papers_dir or papers/md_papers.",
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
    """Parse CLI args, execute workflow, and print stats."""
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    try:
        stats = run_batch_filter(md_papers_dir=args.md_papers_dir)
        print("Batch filter completed.")
        print(f"Source: {stats.get('source_dir', 'N/A')}")
        print(f"Total: {stats.get('total', 0)}")
        print(f"Must Read: {stats.get('must_read', 0)}")
        print(f"Skim: {stats.get('skim', 0)}")
        print(f"Reject: {stats.get('reject', 0)}")
        print(f"Errors: {stats.get('errors', 0)}")
        print(f"Must Read Titles: {stats.get('must_read_titles', [])}")
        return 0 if int(stats.get("errors", 0)) == 0 else 1
    except Exception:
        logging.getLogger(__name__).exception("run_batch_filter script failed.")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
