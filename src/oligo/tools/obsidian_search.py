"""
Obsidian 金库本地文本检索：pathlib 遍历 + 轻量打分，无向量库。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from src.crucible.core.config import load_config

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
    logger.info(
        f"[X-RAY Ripper] Searching vault at: {vault} for query: {query!r}"
    )
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
        f"[X-RAY Ripper] Scan complete. Found {len(ranked)} matching documents."
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


async def search_vault(query: str, top_k: int = 3) -> str:
    settings = load_config()
    vault: Path = settings.vault_root
    if not vault.is_dir():
        return f"[Exocortex error: vault_root is not a directory: {vault}]"
    return await asyncio.to_thread(_ripper_sync, vault, query, top_k)
