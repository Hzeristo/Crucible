# crucible_core/src/oligo/tools/miner_tools.py
"""Background Miner tools (arXiv async fetch + task status)."""

from __future__ import annotations

import asyncio
from typing import Any

from src.crucible.services.fetch_arxiv_workflow import fetch_and_process_arxiv
from src.crucible.services.task_service import TaskStatus, get_task_service


async def arxiv_miner(query: str, max_results: int = 5, **kwargs: Any) -> str:
    """
    Fetch papers from arXiv and process them into Markdown (metadata + abstract).

    This is an async background task: a ``task_id`` is returned immediately;
    use ``check_task_status`` to poll for completion and results.

    Args:
        query: arXiv search query (e.g. ``"memory architecture"``).
        max_results: Maximum number of papers to fetch (default: 5, clamped 1–2000).

    Returns:
        A message including the new ``task_id`` for tracking. Use
        ``check_task_status(task_id)`` to monitor progress and retrieve the body.
    """
    if not (query and str(query).strip()):
        return "[Tool Error]: arxiv_miner requires a non-empty query string."
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, 2000))

    task_service = get_task_service()
    task_id = task_service.create_task("arxiv_fetch")
    work = fetch_and_process_arxiv(
        str(query).strip(), n, task_id=task_id, task_service=task_service
    )
    asyncio.create_task(task_service.run_task(task_id, work))
    return (
        f"[Task Started] Arxiv mining task created: {task_id}\n"
        f"Use check_task_status({task_id!r}) to track progress."
    )


async def _run_daily_with_progress(
    task_id: str,
    arxiv_query: str | None,
    arxiv_max_results: int | None,
    skip_telegram: bool,
) -> str:
    from src.crucible.services.daily_chimera_service import run_daily_pipeline

    task_service = get_task_service()
    return await asyncio.to_thread(
        run_daily_pipeline,
        None,
        arxiv_query=arxiv_query,
        arxiv_max_results=arxiv_max_results,
        skip_telegram=skip_telegram,
        task_id=task_id,
        task_service=task_service,
    )


async def daily_paper_pipeline(
    arxiv_query: str | None = None,
    arxiv_max_results: int | None = None,
    skip_telegram: bool = False,
    **kwargs: Any,
) -> str:
    """
    Run the full daily paper pipeline: arXiv fetch → MinerU PDF ingestion → batch LLM triage → optional Telegram.

    This is a long-running background task (can take tens of minutes to hours). A ``task_id`` is returned
    immediately; use ``check_task_status`` to poll progress and the completion summary.

    Args:
        arxiv_query: Optional override for ``paper_miner.arxiv_query`` in config.
        arxiv_max_results: Optional override for max arXiv API results (1–2000).
        skip_telegram: If True, skip the Telegram morning broadcast (no push); pipeline I/O and triage still run.

    Returns:
        A message with the new ``task_id`` and a reminder to call ``check_task_status``.
    """
    _ = kwargs
    n: int | None
    if arxiv_max_results is None:
        n = None
    else:
        try:
            n = int(arxiv_max_results)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = None

    q_override: str | None = None
    if arxiv_query is not None:
        stripped = str(arxiv_query).strip()
        if stripped:
            q_override = stripped

    task_service = get_task_service()
    task_id = task_service.create_task("daily_pipeline")
    work = _run_daily_with_progress(
        task_id,
        q_override,
        n,
        bool(skip_telegram),
    )
    asyncio.create_task(task_service.run_task(task_id, work))
    return (
        f"[Task Started] Daily pipeline: {task_id}\n"
        f"Use check_task_status({task_id!r}) to track progress."
    )


async def check_task_status(task_id: str, **kwargs: Any) -> str:
    """
    Return persisted status, progress, or final result for a background task.

    Args:
        task_id: Task identifier from a tool that started async work
            (e.g. ``arxiv_miner``).

    Returns:
        Text describing running progress, or completed/failed output; or
        a ``[Tool Error]`` line if the id is missing or unknown.
    """
    tid = (task_id or "").strip()
    if not tid:
        return "[Tool Error]: check_task_status requires a non-empty task_id."
    task_service = get_task_service()
    try:
        task = task_service.get_task_status(tid)
    except FileNotFoundError:
        return f"[Task Error] Unknown task_id: {tid!r}"

    if task.status == TaskStatus.COMPLETED:
        body = task.result or ""
        return f"[Task Completed] {body}"
    if task.status == TaskStatus.FAILED:
        return f"[Task Failed] {task.error}"
    if task.status == TaskStatus.RUNNING:
        msg = (task.progress_message or "").strip() or "Processing..."
        return (
            f"[Task Running] Progress: {task.progress * 100:.0f}% - {msg}"
        )
    if task.status == TaskStatus.PENDING:
        return "[Task Pending] Waiting to start..."
    label = str(task.status.value).upper()
    return f"[Task {label}] Progress: {task.progress * 100:.0f}%"
