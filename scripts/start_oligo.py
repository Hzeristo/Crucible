#!/usr/bin/env python3
"""Oligo FastAPI service launcher - industrial-grade streaming proxy."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root on sys.path (run from repo root: python scripts/start_oligo.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

import uvicorn

from src.crucible.core.config import get_config
from src.oligo.server import app

if __name__ == "__main__":
    settings = get_config()
    uvicorn.run(
        app,
        host=settings.oligo_host,
        port=settings.oligo_port,
        log_level="info",
    )
