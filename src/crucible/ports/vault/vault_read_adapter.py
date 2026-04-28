"""Read-only vault access: authenticated note lookup + markdown search (merged indexer + obsidian_search)."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any

import yaml

from src.crucible.core.config import ChimeraConfig
from src.crucible.core.naming import extract_short_moniker_from_note_filename

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\S+")
# Obsidian wikilinks: [[Note]], [[Note|alias]], [[Note#heading]]
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _wikilink_targets(text: str) -> list[str]:
    out: list[str] = []
    for m in _WIKI_LINK_RE.finditer(text):
        raw = m.group(1)
        if "|" in raw:
            raw = raw.split("|", 1)[0]
        if "#" in raw:
            raw = raw.split("#", 1)[0]
        raw = raw.strip()
        if raw:
            out.append(raw)
    return out


def _graph_edge_targets_from_frontmatter(fm: dict[str, Any]) -> list[str]:
    """Collect targets from Tpl-style ``graph_edges`` (based_on, triggered_by, …)."""
    out: list[str] = []
    ge = fm.get("graph_edges")
    if not isinstance(ge, dict):
        return out
    for v in ge.values():
        if isinstance(v, list):
            for x in v:
                if x is None:
                    continue
                s = str(x).strip()
                if s:
                    out.append(s)
        elif v is not None and str(v).strip():
            out.append(str(v).strip())
    return out


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


class VaultReadAdapter:
    """Vault read + search; does not import other ports."""

    def __init__(self, settings: ChimeraConfig) -> None:
        self._settings = settings
        self._vault_file_cache_max = max(0, int(settings.vault.cache_size))
        # Path string -> (mtime, content); LRU via OrderedDict + move_to_end
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _read_file_cached(self, path: Path) -> str | None:
        """Read UTF-8 note body with mtime-aware LRU cache (repeated scans avoid disk I/O)."""
        if self._vault_file_cache_max <= 0:
            try:
                return path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError, LookupError):
                return None
            except Exception:
                return None
        key = str(path.resolve())
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                cached_mtime, content = entry
                if cached_mtime == mtime:
                    self._cache.move_to_end(key)
                    return content
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, LookupError):
            return None
        except Exception:
            return None
        with self._cache_lock:
            self._cache[key] = (mtime, raw)
            self._cache.move_to_end(key)
            while len(self._cache) > self._vault_file_cache_max:
                self._cache.popitem(last=False)
        return raw

    def _ripper_sync(self, vault: Path, query: str, top_k: int) -> str:
        logger.info("[Vault] Searching vault at: %s for query: %r", vault, query)
        tokens = _tokens(query)
        if not tokens:
            logger.warning("[Vault] Query produced 0 valid tokens.")
            return f"[Exocortex returned 0 results for query: {query}]"

        ranked: list[tuple[int, Path, str]] = []

        for path in vault.rglob("*.md"):
            try:
                rel = path.relative_to(vault)
            except ValueError:
                continue
            if ".obsidian" in rel.parts:
                continue

            raw = self._read_file_cached(path)
            if raw is None:
                continue

            name_lower = path.name.lower()
            body_lower = raw.lower()
            sc = _score_file(name_lower, body_lower, tokens)
            if sc > 0:
                ranked.append((sc, path, raw))

        logger.info(
            "[Vault] Scan complete. Found %s matching documents.", len(ranked)
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

    def _attribute_search_sync(
        self, vault: Path, key: str, value: str, top_k: int
    ) -> str:
        logger.info(
            "[Vault] Scanning vault at: %s for key=%r value=%r top_k=%s (attribute)",
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

            raw = self._read_file_cached(path)
            if raw is None:
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
            logger.warning("[Vault] Vault root missing or not a directory: %s", vault_root)
            return None

        note_candidates = sorted(
            p for p in vault_root.rglob("*.md") if arxiv_id in p.name
        )
        if not note_candidates:
            logger.warning(
                "[Vault] No note matched for arXiv id=%s under %s",
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
                "[Vault] No matching asset PDF found by id prefix. id=%s assets_dir=%s",
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
                    "[Vault] Could not extract short_moniker from note filename: %s",
                    note_path.name,
                )
                continue

            return (note_path.resolve(), short_moniker, selected_pdf)

        logger.warning(
            "[Vault] No valid vault-authenticated paper found for id=%s (note parse failed).",
            arxiv_id,
        )
        return None

    async def search_notes(self, query: str, top_k: int = 3) -> str:
        """Search vault markdown (async wrapper over thread pool)."""
        vault: Path = self._settings.vault_root
        if not vault.is_dir():
            return f"[Exocortex error: vault_root is not a directory: {vault}]"
        return await asyncio.to_thread(self._ripper_sync, vault, query, top_k)

    async def search_by_attribute(self, key: str, value: str, top_k: int = 5) -> str:
        """
        Match notes by YAML frontmatter field: ``key`` must exist and its value (str or list)
        must contain ``value`` (substring, case-insensitive). Returns up to ``top_k`` snippets
        (first ~500 chars of body after frontmatter per file).
        """
        vault: Path = self._settings.vault_root
        if not vault.is_dir():
            return f"[Exocortex error: vault_root is not a directory: {vault}]"
        return await asyncio.to_thread(
            self._attribute_search_sync, vault, key, value, top_k
        )

    def _parse_note_frontmatter_and_body(self, norm: str) -> tuple[dict[str, Any], str]:
        """Return (frontmatter dict, body after frontmatter) from normalized markdown."""
        m = re.match(r"^---\n(.*?)\n---", norm, re.DOTALL)
        if not m:
            return {}, norm
        try:
            loaded = yaml.safe_load(m.group(1))
        except Exception:
            return {}, _body_after_frontmatter(norm, m.end())
        if not isinstance(loaded, dict):
            return {}, _body_after_frontmatter(norm, m.end())
        return loaded, _body_after_frontmatter(norm, m.end())

    def _collect_graph_links(self, fm: dict[str, Any], text_for_wikis: str) -> list[str]:
        """Wikilinks in content + Tpl ``graph_edges``; deduplicated, order preserved."""
        w = _wikilink_targets(text_for_wikis)
        w.extend(_graph_edge_targets_from_frontmatter(fm))
        seen: set[str] = set()
        out: list[str] = []
        for x in w:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _query_graph_sync(
        self,
        node_type: str | None,
        link_pattern: str | None,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        """
        Walk vault markdown, filter by frontmatter ``type`` and/or link pattern, optionally
        expand wikilinks up to (max_depth - 1) hops from seed matches.
        """
        try:
            max_depth = max(1, min(int(max_depth), 8))
        except (TypeError, ValueError):
            max_depth = 2
        max_results = 200

        vault: Path = self._settings.vault_root
        if not vault.is_dir():
            return []

        all_paths: list[Path] = []
        for path in vault.rglob("*.md"):
            try:
                rel = path.relative_to(vault)
            except ValueError:
                continue
            if ".obsidian" in rel.parts:
                continue
            all_paths.append(path)

        stem_to_paths: dict[str, list[Path]] = defaultdict(list)
        for p in all_paths:
            stem_to_paths[p.stem].append(p)

        def first_path_for_stem(stem: str) -> Path | None:
            lst = stem_to_paths.get(stem) or []
            return lst[0] if lst else None

        def read_norm(path: Path) -> str:
            raw = self._read_file_cached(path) or ""
            return _normalize_text_newlines(raw)

        seeds: list[Path] = []
        for path in all_paths:
            norm = read_norm(path)
            if not norm.strip():
                continue
            fm, body = self._parse_note_frontmatter_and_body(norm)
            if node_type is not None:
                nt = str(fm.get("type", "")).strip()
                if nt != str(node_type).strip():
                    continue
            if link_pattern is not None and link_pattern not in norm:
                continue
            seeds.append(path)

        result_set: set[Path] = set()
        for p in seeds:
            result_set.add(p)

        if max_depth > 1 and seeds:
            frontier: list[Path] = list(seeds)
            for _ in range(max_depth - 1):
                if len(result_set) >= max_results:
                    break
                nxt: list[Path] = []
                for p in frontier:
                    norm = read_norm(p)
                    fm, body = self._parse_note_frontmatter_and_body(norm)
                    text = body if body.strip() else norm
                    links = self._collect_graph_links(fm, text)
                    for t in links:
                        child = first_path_for_stem(t) or first_path_for_stem(
                            Path(t).stem
                        )
                        if (
                            child
                            and child not in result_set
                            and len(result_set) < max_results
                        ):
                            result_set.add(child)
                            nxt.append(child)
                if not nxt:
                    break
                frontier = nxt

        rows: list[dict[str, Any]] = []
        for path in sorted(result_set, key=lambda x: str(x).lower()):
            if len(rows) >= max_results:
                break
            norm = read_norm(path)
            fm, body = self._parse_note_frontmatter_and_body(norm)
            text = body if body.strip() else norm
            links = self._collect_graph_links(fm, text)[:20]
            rows.append(
                {
                    "title": path.stem,
                    "path": str(path.resolve()),
                    "type": fm.get("type"),
                    "links": links,
                }
            )
        return rows

    async def query_graph(
        self,
        node_type: str | None = None,
        link_pattern: str | None = None,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Query the vault as a light-weight graph: frontmatter type, wikilinks, ``graph_edges``.

        Args:
            node_type: If set, require YAML ``type:`` to match
                (e.g. knowledge, thought, insight, decision).
            link_pattern: If set, require this substring in the full note.
            max_depth: 1 = seed nodes only; 2+ = include linked notes up to this depth
                (BFS on resolved wikilinks / graph_edges targets by note stem).

        Returns:
            Dicts with title, path, type, and links.
        """
        if not self._settings.vault_root.is_dir():
            return []
        return await asyncio.to_thread(
            self._query_graph_sync, node_type, link_pattern, max_depth
        )
