"""Oligo FastAPI app entry point (re-export for start_oligo compatibility)."""

from src.oligo.api.server import app

__all__: list[str] = ["app"]
