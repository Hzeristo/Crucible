"""Read-only vault access: authenticated note lookup + markdown search (merged indexer + obsidian_search)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from src.crucible.core.config import Settings
from src.crucible.core.naming import extract_short_moniker_from_note_filename

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\S+")


def _tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query.strip()) if t]


def _score_file(name_lower: str, body_lower: str, tokens: list[str]) -> int:
    score = 0
    for t in tokens:
        tl = t.lower()
        if tl in name_lower:
            score += 5
        if tl in body_lower:
            score += 1
    return score


def _snippet(body: str, tokens: list[str], radius: int = 200) -> str:
    if not body:
        return ""
    lower = body.lower()
    best = -1
    best_len = 0
    for t in tokens:
        tl = t.lower()
        pos = lower.find(tl)
        if pos != -1 and (best == -1 or pos < best):
            best = pos
            best_len = len(t)
    if best == -1:
        end = min(radius * 2, len(body))
        frag = body[:end].replace("\n", " ").strip()
        return f"...{frag}..." if len(body) > end else frag
    start = max(0, best - radius)
    end = min(len(body), best + best_len + radius)
    frag = body[start:end].replace("\n", " ").strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{frag}{suffix}"


def _ripper_sync(vault: Path, query: str, top_k: int) -> str:
    logger.info("[X-RAY Ripper] Searching vault at: %s for query: %r", vault, query)
    tokens = _tokens(query)
    if not tokens:
        logger.warning("[X-RAY Ripper] Query produced 0 valid tokens.")
        return f"[Exocortex returned 0 results for query: {query}]"

    ranked: list[tuple[int, Path, str]] = []

    for path in vault.rglob("*.md"):
        try:
            rel = path.relative_to(vault)
        except ValueError:
            continue
        if ".obsidian" in rel.parts:
            continue

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue

        name_lower = path.name.lower()
        body_lower = raw.lower()
        sc = _score_file(name_lower, body_lower, tokens)
        if sc > 0:
            ranked.append((sc, path, raw))

    logger.info(
        "[X-RAY Ripper] Scan complete. Found %s matching documents.", len(ranked)
    )
    ranked.sort(key=lambda x: (-x[0], x[1].name.lower()))
    top = ranked[: max(0, top_k)]

    if not top:
        return f"[Exocortex returned 0 results for query: {query}]"

    blocks: list[str] = []
    for _sc, p, raw in top:
        snip = _snippet(raw, tokens)
        blocks.append(f"[File: {p.name}]\nSnippet: {snip}")
    return "\n\n".join(blocks)


def _normalize_text_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _frontmatter_attr_matches(attr_val: Any, needle: str) -> bool:
    """True if string/list (or other scalar) contains needle (case-insensitive)."""
    if needle == "":
        return False
    n = needle.lower()
    try:
        if isinstance(attr_val, str):
            return n in attr_val.lower()
        if isinstance(attr_val, (list, tuple, set)):
            for item in attr_val:
                try:
                    if n in str(item).lower():
                        return True
                except Exception:
                    continue
            return False
        return n in str(attr_val).lower()
    except Exception:
        return False


def _body_after_frontmatter(normalized: str, end_pos: int) -> str:
    rest = normalized[end_pos:].lstrip("\n")
    return rest if rest else normalized


def _snippet500(body: str) -> str:
    frag = body.replace("\n", " ").strip()
    if len(frag) > 500:
        return f"{frag[:500]}..."
    return frag


def _attribute_search_sync(vault: Path, key: str, value: str, top_k: int) -> str:
    logger.info(
        "[Vault attr] Scanning vault at: %s for key=%r value=%r top_k=%s",
        vault,
        key,
        value,
        top_k,
    )
    k = (key or "").strip()
    if not k or not (value or "").strip():
        return "[Exocortex error: search_by_attribute requires non-empty key and value]"

    hits: list[tuple[Path, str]] = []
    cap = max(0, top_k)

    for path in vault.rglob("*.md"):
        if cap and len(hits) >= cap:
            break
        try:
            rel = path.relative_to(vault)
        except ValueError:
            continue
        if ".obsidian" in rel.parts:
            continue

        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, LookupError):
            continue
        except Exception:
            continue

        try:
            norm = _normalize_text_newlines(raw)
            m = re.match(r"^---\n(.*?)\n---", norm, re.DOTALL)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1))
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            try:
                attr_val = fm.get(k)
            except Exception:
                continue
            if attr_val is None:
                continue
            if not _frontmatter_attr_matches(attr_val, value.strip()):
                continue
            body = _body_after_frontmatter(norm, m.end())
        except Exception:
            continue

        try:
            snip = _snippet500(body)
        except Exception:
            continue

        try:
            hits.append((path, snip))
        except Exception:
            continue

    if not hits:
        return f"[Exocortex returned 0 attribute matches for key={key!r} value={value!r}]"

    blocks: list[str] = []
    for p, snip in hits:
        try:
            blocks.append(f"[File: {p.name}]\nSnippet: {snip}")
        except Exception:
            continue
    if not blocks:
        return f"[Exocortex returned 0 attribute matches for key={key!r} value={value!r}]"
    return "\n\n".join(blocks)


class VaultReadAdapter:
    """Vault read + search; does not import other ports."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def find_authenticated_paper(
        self, arxiv_id: str
    ) -> tuple[Path, str, Path] | None:
        """
        Locate a paper note in vault (recursive) and verify its paired PDF asset.

        Returns:
            (note_md_path, short_moniker, pdf_path) when both note + PDF exist, else None.
        """
        vault_root = self._settings.vault_root
        if not vault_root.exists() or not vault_root.is_dir():
            logger.warning("Vault root missing or not a directory: %s", vault_root)
            return None

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

        vault_assets_dir = self._settings.require_path("vault_assets_dir")
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
            short_moniker = extract_short_moniker_from_note_filename(
                note_path.name, arxiv_id
            )
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

    async def search_notes(self, query: str, top_k: int = 3) -> str:
        """Search vault markdown (async wrapper over thread pool)."""
        vault: Path = self._settings.vault_root
        if not vault.is_dir():
            return f"[Exocortex error: vault_root is not a directory: {vault}]"
        return await asyncio.to_thread(_ripper_sync, vault, query, top_k)

    async def search_by_attribute(self, key: str, value: str, top_k: int = 5) -> str:
        """
        Match notes by YAML frontmatter field: ``key`` must exist and its value (str or list)
        must contain ``value`` (substring, case-insensitive). Returns up to ``top_k`` snippets
        (first ~500 chars of body after frontmatter per file).
        """
        vault: Path = self._settings.vault_root
        if not vault.is_dir():
            return f"[Exocortex error: vault_root is not a directory: {vault}]"
        return await asyncio.to_thread(_attribute_search_sync, vault, key, value, top_k)
