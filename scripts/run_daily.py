"""Thin CLI entrypoint for the daily Chimera pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.crucible.core.config import load_config  # noqa: E402
from src.crucible.services.daily_chimera_service import run_daily_pipeline  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_config()
    settings.ensure_directories()
    run_daily_pipeline(settings=settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
