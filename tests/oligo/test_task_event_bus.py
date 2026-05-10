"""TaskService event bus tests."""

from __future__ import annotations

import asyncio

from src.crucible.core.schemas import TaskEvent, TaskEventType
from src.crucible.services.task_service import TaskService, TaskStatus


def _mk_event(
    task_id: str,
    task_type: str = "arxiv_fetch",
    event_type: TaskEventType = TaskEventType.STAGE_PROGRESS,
) -> TaskEvent:
    return TaskEvent(
        event_type=event_type,
        task_id=task_id,
        task_type=task_type,
        stage_id=None,
        stage_label=None,
        overall_progress=0.4,
        message="progress",
        error=None,
        timestamp_ms=1730000000000,
    )


def test_emit_event_pushes_to_all_subscribers(tmp_path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.create_task("arxiv_fetch")

    q1 = service.subscribe()
    q2 = service.subscribe()
    event = _mk_event(task_id=task_id)

    asyncio.run(service.emit_event(event))

    got1 = q1.get_nowait()
    got2 = q2.get_nowait()
    assert got1.event_type == TaskEventType.STAGE_PROGRESS
    assert got2.event_type == TaskEventType.STAGE_PROGRESS
    assert got1.task_id == task_id
    assert got2.task_id == task_id


def test_start_stage_emits_stage_start_event_and_persists_progress(tmp_path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.create_task("daily_pipeline")

    running_task = service.get_task_status(task_id)
    running_task.status = TaskStatus.RUNNING
    service._save_task(running_task)

    q = service.subscribe()
    asyncio.run(
        service.start_stage(
            task_id=task_id,
            stage_id="fetch",
            stage_label="Fetching from arXiv",
            overall_progress=0.2,
        )
    )

    persisted = service.get_task_status(task_id)
    assert persisted.progress == 0.2
    assert persisted.progress_message == "Fetching from arXiv"

    event = q.get_nowait()
    assert event.event_type == TaskEventType.STAGE_START
    assert event.task_id == task_id
    assert event.stage_id == "fetch"
    assert event.stage_label == "Fetching from arXiv"
    assert event.overall_progress == 0.2


def test_emit_completed_sets_task_status_completed(tmp_path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.create_task("smoke_test")

    running_task = service.get_task_status(task_id)
    running_task.status = TaskStatus.RUNNING
    service._save_task(running_task)

    q = service.subscribe()
    asyncio.run(service.emit_completed(task_id, summary="Smoke complete"))

    task = service.get_task_status(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.progress == 1.0
    assert task.result == "Smoke complete"
    assert task.completed_at is not None

    event = q.get_nowait()
    assert event.event_type == TaskEventType.COMPLETED
    assert event.task_id == task_id
    assert event.message == "Smoke complete"


def test_unsubscribe_stops_event_delivery(tmp_path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.create_task("daily_pipeline")

    q = service.subscribe()
    service.unsubscribe(q)

    asyncio.run(service.emit_event(_mk_event(task_id=task_id, task_type="daily_pipeline")))
    assert q.empty()


def test_queue_full_old_subscriber_does_not_block_new_subscriber(tmp_path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.create_task("arxiv_fetch")
    old_q = service.subscribe()
    new_q = service.subscribe()

    for _ in range(100):
        old_q.put_nowait(_mk_event(task_id=task_id))

    sent = _mk_event(task_id=task_id, event_type=TaskEventType.COMPLETED)
    asyncio.run(service.emit_event(sent))

    assert old_q.qsize() == 100
    got_new = new_q.get_nowait()
    assert got_new.event_type == TaskEventType.COMPLETED
    assert got_new.task_id == task_id
