"""Async background task registry with JSON persistence (Miner / Oligo tools)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

_task_service_singleton: "TaskService | None" = None


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
            p = max(0.0, min(1.0, float(progress)))
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
            task = self._load_task(task_id)
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.result = None
            task.progress_message = None
        else:
            task = self._load_task(task_id)
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.progress_message = None
            task.result = result
            task.error = None
        task.completed_at = datetime.now().isoformat()
        self._save_task(task)

    def get_task_status(self, task_id: str) -> Task:
        """Load current task state from disk."""
        return self._load_task(task_id)
