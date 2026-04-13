"""Read-only vault indexer for authenticated deep-read targets."""

from __future__ import annotations

import logging
from pathlib import Path

from src.crucible.core.config import Settings

logger = logging.getLogger(__name__)


def _extract_short_moniker(filename: str, arxiv_id: str) -> str | None:
    """
    Extract moniker from ``{arxiv_id}-*.md`` filename with defensive parsing.

    This parser intentionally avoids regex overfitting and handles edge cases like:
    - unexpected whitespace around basename
    - mixed-case extension
    - accidental extra separators after ``{arxiv_id}-``
    """
    name = (filename or "").strip()
    if not name:
        return None

    lower_name = name.lower()
    if not lower_name.endswith(".md"):
        return None

    prefix = f"{arxiv_id}-"
    if not name.startswith(prefix):
        return None

    # Keep all payload between "{arxiv_id}-" and trailing ".md".
    body = name[: -len(".md")]
    moniker = body[len(prefix) :].strip()
    moniker = moniker.strip("-_ ")
    if not moniker:
        return None
    return moniker


def find_paper_in_vault(arxiv_id: str, settings: Settings) -> tuple[Path, str, Path] | None:
    """
    Locate a paper note in vault (recursive) and verify its paired PDF asset.

    Returns:
        (note_md_path, short_moniker, pdf_path) when both note + PDF exist,
        otherwise ``None``.
    """
    vault_root = settings.vault_root
    if not vault_root.exists() or not vault_root.is_dir():
        logger.warning("Vault root missing or not a directory: %s", vault_root)
        return None

    # Ignore directory naming and scan all markdown notes recursively.
    note_candidates = sorted(
        p for p in vault_root.rglob("*.md") if arxiv_id in p.name
    )
    if not note_candidates:
        logger.warning(
            "No note matched in vault for arXiv id=%s under %s",
            arxiv_id,
            vault_root,
        )
        return None

    vault_assets_dir = settings.require_path("vault_assets_dir")
    asset_candidates = sorted(vault_assets_dir.rglob(f"{arxiv_id}*.pdf"))
    if not asset_candidates:
        asset_candidates = sorted(vault_assets_dir.rglob(f"{arxiv_id}*.PDF"))
    if not asset_candidates:
        logger.warning(
            "No matching asset PDF found by id prefix in vault assets. id=%s assets_dir=%s",
            arxiv_id,
            vault_assets_dir,
        )
        return None

    selected_pdf = asset_candidates[0].resolve()
    for note_path in note_candidates:
        if not note_path.is_file():
            continue
        short_moniker = _extract_short_moniker(note_path.name, arxiv_id)
        if not short_moniker:
            logger.warning(
                "Could not extract short_moniker from note filename: %s",
                note_path.name,
            )
            continue

        return (note_path.resolve(), short_moniker, selected_pdf)

    logger.warning(
        "No valid vault-authenticated paper found for id=%s (note parse failed).",
        arxiv_id,
    )
    return None
