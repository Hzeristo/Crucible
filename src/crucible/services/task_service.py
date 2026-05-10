"""Async background task registry with JSON persistence (Miner / Oligo tools)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from src.crucible.core.schemas import TaskEvent, TaskEventType

_task_service_singleton: "TaskService | None" = None

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str
    type: str
    status: TaskStatus
    progress: float = Field(ge=0.0, le=1.0)
    progress_message: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None


def set_task_service(service: "TaskService") -> None:
    global _task_service_singleton
    _task_service_singleton = service


def get_task_service() -> "TaskService":
    """Return configured TaskService, or a default under ``~/.chimera/tasks``."""
    global _task_service_singleton
    if _task_service_singleton is None:
        from src.crucible.core.platform import get_chimera_root

        _task_service_singleton = TaskService(get_chimera_root() / "tasks")
    return _task_service_singleton


class TaskService:
    def __init__(self, tasks_dir: Path) -> None:
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._event_queue: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers: set[asyncio.Queue[TaskEvent]] = set()

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def _save_task(self, task: Task) -> None:
        path = self._task_path(task.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = task.model_dump(mode="json")
        text = json.dumps(data, indent=2, ensure_ascii=False)
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)

    def _load_task(self, task_id: str) -> Task:
        path = self._task_path(task_id)
        if not path.is_file():
            raise FileNotFoundError(f"Task not found: {task_id}")
        raw = path.read_text(encoding="utf-8")
        return Task.model_validate_json(raw)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _safe_progress(progress: float) -> float:
        return max(0.0, min(1.0, float(progress)))

    async def emit_event(self, event: TaskEvent) -> None:
        """Publish one task event to all subscribers (non-blocking drop on backpressure)."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("[Task] Internal event queue full, dropping event: %s", event.event_type.value)
        for subscriber in list(self._subscribers):
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[Task] Subscriber queue full, dropping event")

    def subscribe(self) -> asyncio.Queue[TaskEvent]:
        """Register one subscriber queue for task events."""
        q: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TaskEvent]) -> None:
        self._subscribers.discard(q)

    async def start_stage(
        self,
        task_id: str,
        stage_id: str,
        stage_label: str,
        overall_progress: float = 0.0,
        message: str | None = None,
    ) -> None:
        """Mark a new stage start and persist stage label/progress."""
        p = self._safe_progress(overall_progress)
        task = self._load_task(task_id)
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING
        task.progress = p
        task.progress_message = stage_label
        self._save_task(task)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.STAGE_START,
                task_id=task_id,
                task_type=task.type,
                stage_id=stage_id,
                stage_label=stage_label,
                overall_progress=p,
                message=message,
                timestamp_ms=self._now_ms(),
            )
        )

    async def emit_stage_progress(
        self,
        task_id: str,
        *,
        stage_id: str | None = None,
        stage_label: str | None = None,
        overall_progress: float = 0.0,
        message: str | None = None,
    ) -> None:
        """Emit optional fine-grained stage progress event."""
        p = self._safe_progress(overall_progress)
        task = self._load_task(task_id)
        self.update_progress(task_id, p, message if message is not None else stage_label)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.STAGE_PROGRESS,
                task_id=task_id,
                task_type=task.type,
                stage_id=stage_id,
                stage_label=stage_label,
                overall_progress=p,
                message=message,
                timestamp_ms=self._now_ms(),
            )
        )

    async def emit_created(self, task_id: str, message: str | None = None) -> None:
        """Emit CREATED event for an existing task."""
        task = self._load_task(task_id)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.CREATED,
                task_id=task.id,
                task_type=task.type,
                stage_id=None,
                stage_label=None,
                overall_progress=task.progress,
                message=message or "Task created.",
                error=None,
                timestamp_ms=self._now_ms(),
            )
        )

    async def emit_completed(self, task_id: str, summary: str | None = None) -> None:
        """Mark task completed and emit COMPLETED event."""
        task = self._load_task(task_id)
        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.progress_message = None
        task.result = summary if summary is not None else task.result
        task.error = None
        task.completed_at = datetime.now().isoformat()
        self._save_task(task)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.COMPLETED,
                task_id=task.id,
                task_type=task.type,
                stage_id=None,
                stage_label=None,
                overall_progress=task.progress,
                message=task.result,
                error=None,
                timestamp_ms=self._now_ms(),
            )
        )

    async def emit_failed(self, task_id: str, error: str) -> None:
        """Mark task failed and emit FAILED event."""
        task = self._load_task(task_id)
        task.status = TaskStatus.FAILED
        task.progress_message = None
        task.error = str(error)
        task.result = None
        task.completed_at = datetime.now().isoformat()
        self._save_task(task)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.FAILED,
                task_id=task.id,
                task_type=task.type,
                stage_id=None,
                stage_label=None,
                overall_progress=task.progress,
                message=None,
                error=task.error,
                timestamp_ms=self._now_ms(),
            )
        )

    async def emit_cancelled(self, task_id: str, reason: str | None = None) -> None:
        """Mark task cancelled and emit CANCELLED event."""
        task = self._load_task(task_id)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return
        task.status = TaskStatus.CANCELLED
        task.progress_message = None
        task.error = reason or "Task cancelled."
        task.completed_at = datetime.now().isoformat()
        self._save_task(task)
        await self.emit_event(
            TaskEvent(
                event_type=TaskEventType.CANCELLED,
                task_id=task.id,
                task_type=task.type,
                stage_id=None,
                stage_label=None,
                overall_progress=task.progress,
                message=reason,
                error=task.error,
                timestamp_ms=self._now_ms(),
            )
        )

    def create_task(self, task_type: str) -> str:
        """Create a new task; returns ``task_id`` (8-char prefix of UUID)."""
        for _ in range(8):
            task_id = str(uuid.uuid4())[:8]
            if self._task_path(task_id).exists():
                continue
            task = Task(
                id=task_id,
                type=task_type,
                status=TaskStatus.PENDING,
                progress=0.0,
                progress_message=None,
                result=None,
                error=None,
                created_at=datetime.now().isoformat(),
                completed_at=None,
            )
            self._save_task(task)
            return task_id
        raise RuntimeError("Failed to allocate a unique task id")

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str | None = None,
    ) -> None:
        """Update progress (0.0–1.0) and optional status line while task is running."""
        try:
            p = self._safe_progress(progress)
        except (TypeError, ValueError):
            return
        task = self._load_task(task_id)
        if task.status != TaskStatus.RUNNING:
            return
        task.progress = p
        if message is not None:
            task.progress_message = message
        self._save_task(task)

    async def run_task(self, task_id: str, work: Awaitable[str]) -> None:
        """Run async work, persist status/result/error."""
        task = self._load_task(task_id)
        task.status = TaskStatus.RUNNING
        task.progress = 0.0
        task.progress_message = "Starting..."
        self._save_task(task)
        try:
            result = await work
        except Exception as e:
            await self.emit_failed(task_id, str(e))
        else:
            await self.emit_completed(task_id, summary=result)

    async def cancel_task(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> None:
        """Mark a running/pending task as cancelled and emit a cancellation event."""
        await self.emit_cancelled(task_id, reason=reason)

    def get_task_status(self, task_id: str) -> Task:
        """Load current task state from disk."""
        return self._load_task(task_id)
