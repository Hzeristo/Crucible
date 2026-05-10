"""Manual smoke test for task progress stream and UI panel timer."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.crucible.services.task_service import get_task_service  # noqa: E402

logger = logging.getLogger(__name__)


async def main() -> int:
    """
    Manual smoke:
    1) Start Oligo + Astrocyte
    2) Run this script
    3) Verify ActiveTaskPanel stage timer resets on stage switches and disappears 5s after completion
    """
    task_service = get_task_service()
    task_id = task_service.create_task("smoke_test")
    await task_service.emit_created(task_id, message="Smoke task created.")

    stages = [
        ("init", "Initializing", 0.0, 1.0),
        ("phase_a", "Phase A: simulating fetch", 0.25, 2.5),
        ("phase_b", "Phase B: simulating compute", 0.5, 3.0),
        ("phase_c", "Phase C: simulating finalize", 0.85, 1.5),
    ]
    try:
        for stage_id, label, progress, duration in stages:
            await task_service.start_stage(task_id, stage_id, label, progress)
            await asyncio.sleep(duration)
    except Exception as exc:
        await task_service.emit_failed(task_id, error=str(exc))
        raise

    await task_service.emit_completed(task_id, summary="Smoke complete")
    logger.info("[SmokeTask] completed task_id=%s", task_id)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    raise SystemExit(asyncio.run(main()))
