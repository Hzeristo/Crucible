# crucible_core/src/oligo/tools/vault_tools.py
"""Vault search tools (require a process-wide adapter from Oligo lifespan or tests)."""

from __future__ import annotations

from typing import Any, Protocol

class _VaultToolPort(Protocol):
    """Structural type for vault search backends (``VaultReadAdapter`` or test doubles)."""

    async def search_notes(self, query: str, top_k: int = 3) -> str: ...

    async def search_by_attribute(self, key: str, value: str, top_k: int = 5) -> str: ...

    async def query_graph(
        self,
        node_type: str | None = None,
        link_pattern: str | None = None,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]: ...


_vault_adapter: _VaultToolPort | None = None


def set_vault_adapter(adapter: _VaultToolPort | None) -> None:
    """Bind the global vault adapter (e.g. from FastAPI lifespan)."""
    global _vault_adapter
    _vault_adapter = adapter


async def search_vault(query: str, **kwargs: Any) -> str:
    """
    Search the Obsidian vault for notes whose bodies match the query (keyword-style).

    Args:
        query: Keywords to search for.
        top_k: Optional; max hits (default 3), from JSON args.

    Returns:
        Matching note snippets as formatted text from the vault adapter.
    """
    if _vault_adapter is None:
        return "[Tool Error] Vault adapter not initialized"
    try:
        q = str(query or "").strip()
        if not q:
            return "[Tool Error]: search_vault requires a non-empty query string."
        top_k = int(kwargs.get("top_k", 3))
        return await _vault_adapter.search_notes(q, top_k)
    except (TypeError, ValueError) as e:
        return f"Error: Tool 'search_vault' invalid args: {e}"


async def search_vault_attribute(key: str, value: str, **kwargs: Any) -> str:
    """
    Search the vault by YAML frontmatter (key must exist; value matched as substring).

    Args:
        key: Frontmatter field name (e.g. tags, author).
        value: Substring to match within the field value.
        top_k: Optional; max hits (default 5), from JSON args.

    Returns:
        Matching note snippets from the vault adapter.
    """
    if _vault_adapter is None:
        return "[Tool Error] Vault adapter not initialized"
    try:
        attr_key = str(key or "").strip()
        attr_val = str(value or "").strip()
        top_k = int(kwargs.get("top_k", 5))
        return await _vault_adapter.search_by_attribute(attr_key, attr_val, top_k)
    except (TypeError, ValueError) as e:
        return f"Error: Tool 'search_vault_attribute' invalid args: {e}"


async def obsidian_graph_query(
    node_type: str | None = None,
    link_pattern: str | None = None,
    **kwargs: Any,
) -> str:
    """
    Query the Obsidian graph for nodes and links (frontmatter type, wikilinks, graph_edges).

    Args:
        node_type: Filter by YAML ``type`` (e.g. knowledge, thought, insight, decision).
        link_pattern: Optional substring that must appear in the full note text.
        max_depth: BFS depth from seed matches (default 2), from JSON args.

    Returns:
        Formatted list of nodes (title, type, links); at most 10 shown in full.
    """
    if _vault_adapter is None:
        return "[Tool Error] Vault adapter not initialized"
    nt = None if node_type is None else str(node_type).strip() or None
    lp = None if link_pattern is None else str(link_pattern).strip() or None
    try:
        md = int(kwargs.get("max_depth", 2))
    except (TypeError, ValueError):
        md = 2
    md = max(1, min(md, 8))

    try:
        results = await _vault_adapter.query_graph(nt, lp, md)
    except (TypeError, ValueError) as e:
        return f"Error: Tool 'obsidian_graph_query' invalid args: {e}"

    if not results:
        bits: list[str] = []
        if nt:
            bits.append(f"type={nt!r}")
        if lp:
            bits.append(f"pattern={lp!r}")
        tail = ", ".join(bits) if bits else "no matching notes in vault"
        return f"[Graph Query] No nodes found ({tail})"

    out = f"[Graph Query] Found {len(results)} nodes:\n\n"
    for node in results[:10]:
        title = node.get("title", "?")
        ntype = node.get("type", "")
        links = node.get("links") or []
        line = f"- {title}"
        if ntype is not None and str(ntype) != "":
            line += f" ({ntype})"
        out += line + "\n"
        if links:
            preview = [str(x) for x in links[:5]]
            out += f"  Links: {', '.join(preview)}\n"
    if len(results) > 10:
        out += f"\n… and {len(results) - 10} more (showing first 10).\n"
    return out
