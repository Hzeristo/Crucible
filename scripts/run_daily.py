"""Thin CLI entrypoint for the daily Chimera pipeline."""

# 使用示例:
#   python scripts/run_daily.py

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    """Return repository root based on this script location."""
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.miners.paperminer.workflows.chimera_daily import run_daily_pipeline  # noqa: E402


def main() -> int:
    """Configure logging and run the daily pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    run_daily_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
