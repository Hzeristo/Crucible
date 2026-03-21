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
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

import uvicorn

from src.oligo.server import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=33333,
        log_level="info",
    )
