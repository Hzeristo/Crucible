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
                "query": {"type": "str", "required": True},
                "top_k": {"type": "int", "required": False},
            },
            concurrency_safe=True,
            long_running=False,
            examples=['<CMD:search_vault({"query": "memory architecture"})>'],
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
                "key": {"type": "str", "required": True},
                "value": {"type": "str", "required": True},
                "top_k": {"type": "int", "required": False},
            },
            concurrency_safe=True,
            long_running=False,
            examples=[
                '<CMD:search_vault_attribute({"key": "tags", "value": "ml"})>',
            ],
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
                "node_type": {"type": "str", "required": False},
                "link_pattern": {"type": "str", "required": False},
                "max_depth": {"type": "int", "required": False},
            },
            concurrency_safe=True,
            long_running=False,
            examples=[
                '<CMD:obsidian_graph_query({"node_type": "insight"})>',
            ],
        ),
    )
    reg.register(
        web_search,
        ToolSpec(
            name="web_search",
            description="Search the web using DuckDuckGo (no API key).",
            args_schema={"query": {"type": "str", "required": True}},
            concurrency_safe=True,
            long_running=False,
            examples=['<CMD:web_search({"query": "latest RAG benchmarks"})>'],
        ),
    )
    reg.register(
        arxiv_miner,
        ToolSpec(
            name="arxiv_miner",
            description=(
                "Fetch papers from arXiv and process them into Markdown; returns a "
                "task_id immediately — use check_task_status to poll."
            ),
            args_schema={
                "query": {"type": "str", "required": True},
                "max_results": {"type": "int", "required": False},
            },
            concurrency_safe=False,
            long_running=True,
            examples=['<CMD:arxiv_miner({"query": "graph neural networks"})>'],
        ),
    )
    reg.register(
        daily_paper_pipeline,
        ToolSpec(
            name="daily_paper_pipeline",
            description=(
                "Run the full daily paper pipeline (long-running); returns task_id — "
                "use check_task_status."
            ),
            args_schema={
                "arxiv_query": {"type": "str", "required": False},
                "arxiv_max_results": {"type": "int", "required": False},
                "skip_telegram": {"type": "bool", "required": False},
            },
            concurrency_safe=False,
            long_running=True,
            examples=["<CMD:daily_paper_pipeline({})>"],
        ),
    )
    reg.register(
        check_task_status,
        ToolSpec(
            name="check_task_status",
            description=(
                "Return status or result for a background task (read-only poll)."
            ),
            args_schema={"task_id": {"type": "str", "required": True}},
            concurrency_safe=True,
            long_running=False,
            examples=['<CMD:check_task_status({"task_id": "abc123"})>'],
        ),
    )


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
        _register_default_tools(_global_registry)
    return _global_registry
