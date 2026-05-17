# crucible_core/src/oligo/tools/registry.py
"""Central tool registry with ToolSpec metadata (TP.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection

from src.crucible.core.schemas import PlannedToolCall, ToolSpec
from src.oligo.tools.miner_tools import (
    arxiv_miner,
    check_task_status,
    daily_paper_pipeline,
)
from src.oligo.tools.vault_tools import (
    obsidian_graph_query,
    read_vault_file,
    search_vault,
    search_vault_attribute,
)
from src.oligo.tools.web_search import web_search

ToolFn = Callable[..., Awaitable[str]]


class ToolRegistry:
    """工具注册表, 替代当前的裸 dict TOOL_REGISTRY（执行期仍通过 TOOL_REGISTRY 派生）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(self, func: ToolFn, spec: ToolSpec) -> ToolFn:
        """注册工具"""
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self._tools[spec.name] = func
        self._specs[spec.name] = spec
        return func

    def get(self, name: str) -> ToolFn | None:
        return self._tools.get(name)

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def registered_names(self) -> tuple[str, ...]:
        """已注册工具名（插入顺序）。"""
        return tuple(self._tools.keys())

    def list_specs(self, allowed: Collection[str] | None = None) -> list[ToolSpec]:
        """列出所有 spec, 可选白名单过滤（``allowed`` 为 None 表示不过滤）。"""
        out: list[ToolSpec] = []
        for name in self._tools:
            if allowed is not None and name not in allowed:
                continue
            spec = self._specs.get(name)
            if spec is not None:
                out.append(spec)
        return out

    def is_concurrency_safe(self, name: str) -> bool:
        spec = self._specs.get(name)
        return spec.concurrency_safe if spec else False


def partition_tool_calls(
    calls: list[PlannedToolCall],
    registry: ToolRegistry,
) -> list[list[PlannedToolCall]]:
    """
    按 ``concurrency_safe`` 把 calls 分批（连续 safe 同批并发；unsafe 独占一批）。

    保持原始顺序；空输入返回 ``[]``。
    """
    if not calls:
        return []

    batches: list[list[PlannedToolCall]] = []
    current_batch: list[PlannedToolCall] = []

    for call in calls:
        is_safe = registry.is_concurrency_safe(call.tool_name)
        if is_safe:
            current_batch.append(call)
        else:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
            batches.append([call])

    if current_batch:
        batches.append(current_batch)

    return batches


_global_registry: ToolRegistry | None = None


def _register_default_tools(reg: ToolRegistry) -> None:
    reg.register(
        search_vault,
        ToolSpec(
            name="search_vault",
            description=(
                "Search the Obsidian vault for notes whose bodies match the query "
                "(keyword-style)."
            ),
            args_schema={
                "query": {
                    "type": "str",
                    "required": True,
                    "help": "Keywords to match in note bodies.",
                },
                "top_k": {
                    "type": "int",
                    "required": False,
                    "help": "Maximum snippets to return (default 3).",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )
    reg.register(
        read_vault_file,
        ToolSpec(
            name="read_vault_file",
            description="Read the full content of a vault note by its path.",
            args_schema={
                "path": {
                    "type": "str",
                    "required": True,
                    "help": "Path to the note relative to the vault root (or absolute under vault).",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )
    reg.register(
        search_vault_attribute,
        ToolSpec(
            name="search_vault_attribute",
            description=(
                "Search the vault by YAML frontmatter (key must exist; value matched "
                "as substring)."
            ),
            args_schema={
                "key": {
                    "type": "str",
                    "required": True,
                    "help": "Frontmatter field name (e.g. tags, type).",
                },
                "value": {
                    "type": "str",
                    "required": True,
                    "help": "Substring to find within that field's value.",
                },
                "top_k": {
                    "type": "int",
                    "required": False,
                    "help": "Maximum hits (default 5).",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )
    reg.register(
        obsidian_graph_query,
        ToolSpec(
            name="obsidian_graph_query",
            description=(
                "Query the Obsidian graph for nodes and links (frontmatter type, "
                "wikilinks, graph_edges)."
            ),
            args_schema={
                "node_type": {
                    "type": "str",
                    "required": False,
                    "help": "Filter nodes by YAML type field (nullable to skip).",
                },
                "link_pattern": {
                    "type": "str",
                    "required": False,
                    "help": "Substring that must appear in the note body (nullable to skip).",
                },
                "max_depth": {
                    "type": "int",
                    "required": False,
                    "help": "Graph BFS depth from seed nodes (clamped 1–8 on execution; default 2).",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )
    reg.register(
        web_search,
        ToolSpec(
            name="web_search",
            description="Search the web using DuckDuckGo (no API key).",
            args_schema={
                "query": {
                    "type": "str",
                    "required": True,
                    "help": "Search keywords (must be non-empty after trim).",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )
    reg.register(
        arxiv_miner,
        ToolSpec(
            name="arxiv_miner",
            description=(
                "Fetch papers from arXiv and process them into Markdown; returns a "
                "task_id immediately — poll with the task-status tool from the same list."
            ),
            args_schema={
                "query": {
                    "type": "str",
                    "required": True,
                    "help": "arXiv / literature search query string.",
                },
                "max_results": {
                    "type": "int",
                    "required": False,
                    "help": "How many papers to fetch (1–2000; default 5).",
                },
            },
            concurrency_safe=False,
            long_running=True,
        ),
    )
    reg.register(
        daily_paper_pipeline,
        ToolSpec(
            name="daily_paper_pipeline",
            description=(
                "Run the full daily paper pipeline (long-running); returns task_id — "
                "poll with the task-status tool from the same list."
            ),
            args_schema={
                "arxiv_query": {
                    "type": "str",
                    "required": False,
                    "help": "Optional override for the configured arXiv query.",
                },
                "arxiv_max_results": {
                    "type": "int",
                    "required": False,
                    "help": "Optional cap on arXiv results (1–2000).",
                },
                "skip_telegram": {
                    "type": "bool",
                    "required": False,
                    "help": "If true, skip the Telegram broadcast step.",
                },
            },
            concurrency_safe=False,
            long_running=True,
        ),
    )
    reg.register(
        check_task_status,
        ToolSpec(
            name="check_task_status",
            description=(
                "Return status or result for a background task (read-only poll)."
            ),
            args_schema={
                "task_id": {
                    "type": "str",
                    "required": True,
                    "help": "Identifier returned when starting a long-running task.",
                },
            },
            concurrency_safe=True,
            long_running=False,
        ),
    )


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
        _register_default_tools(_global_registry)
    return _global_registry
