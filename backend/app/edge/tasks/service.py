"""Task status service.

Owns task status transitions on the edge tier once a task has started.
The cloud owns tasks in 'assigned' state; once actual_start is set,
the edge owns all further transitions. Every change is queued for the
cloud as a task_status message in the same transaction.

Valid transitions:
  assigned     -> in_progress  (start)
  in_progress  -> paused       (pause)
  in_progress  -> blocked      (block)
  in_progress  -> done         (complete — requires operator confirmation)
  paused       -> in_progress  (resume)
  blocked      -> in_progress  (unblock)
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.core.errors import InvalidStatusTransitionError, NotFoundError
from app.db.models.edge.assignment import EdgeTask
from app.edge.eta.revision import revise_if_triggered
from app.edge.outbox import queue_audit, queue_task_status
from app.shared.enums import AuditAction, QuantityUnit, TaskStatus

log = logging.getLogger("safe2go.tasks")

# Allowed transitions: current_status -> set of allowed next statuses
_ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: {TaskStatus.PAUSED, TaskStatus.BLOCKED, TaskStatus.DONE},
    TaskStatus.PAUSED: {TaskStatus.IN_PROGRESS},
    TaskStatus.BLOCKED: {TaskStatus.IN_PROGRESS},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}

def _held_minutes(task: EdgeTask, now: datetime) -> float:
    if task.hold_started_at is None:
        log.warning("Pause or block start time missing, counted as zero", extra={"task_id": task.task_id})
        return 0.0
    return max(0.0, (now - task.hold_started_at).total_seconds() / 60.0)


async def get_task(task_id: str, session: AsyncSession) -> EdgeTask:
    result = await session.execute(select(EdgeTask).where(EdgeTask.task_id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError(f"Task {task_id} not found.")
    return task


async def apply_action(
    task_id: str,
    action: str,
    session: AsyncSession,
    actor_user_id: str | None = None,
) -> EdgeTask:
    """Apply a lifecycle action to a task. Returns the updated task."""
    task = await get_task(task_id, session)
    current = TaskStatus(task.status)
    now = sim_now()

    target = _action_to_status(action, current)

    allowed = _ALLOWED.get(current, set())
    if target not in allowed:
        raise InvalidStatusTransitionError(
            f"Cannot transition task from '{current}' via '{action}'.",
            details={"current_status": current, "action": action},
        )

    # Side effects per transition. A pause or block period is timed from
    # hold_started_at, stored on the task so a restart does not lose it.
    if action == "start":
        task.actual_start = now
    elif action in ("pause", "block"):
        task.hold_started_at = now
    elif action in ("resume", "unblock"):
        held = _held_minutes(task, now)
        if current == TaskStatus.PAUSED:
            task.paused_minutes = (task.paused_minutes or 0.0) + held
        else:
            task.blocked_minutes = (task.blocked_minutes or 0.0) + held
        task.hold_started_at = None
    elif action == "complete":
        task.actual_end = now

    task.status = target.value
    await session.flush()
    queue_task_status(session, task)
    queue_audit(session, actor_user_id, AuditAction.TASK_STATUS_CHANGE, "task", task_id)
    log.info(
        "Task status changed",
        extra={"task_id": task_id, "from": current, "to": target, "action": action},
    )
    return task


def _action_to_status(action: str, current: TaskStatus) -> TaskStatus:
    mapping = {
        "start": TaskStatus.IN_PROGRESS,
        "pause": TaskStatus.PAUSED,
        "resume": TaskStatus.IN_PROGRESS,
        "block": TaskStatus.BLOCKED,
        "unblock": TaskStatus.IN_PROGRESS,
        "complete": TaskStatus.DONE,
    }
    if action not in mapping:
        raise InvalidStatusTransitionError(
            f"Unknown action '{action}'.",
            details={"action": action},
        )
    return mapping[action]


def cycle_quantity(quantity_unit: str, cycle_payload_pct: float, bucket_capacity: float) -> float:
    """Quantity moved by one completed cycle: one load, or the bucket volume actually filled."""
    if quantity_unit == QuantityUnit.LOADS.value:
        return 1.0
    return (cycle_payload_pct / 100.0) * bucket_capacity


async def record_cycle(
    task: EdgeTask,
    cycle_payload_pct: float,
    bucket_capacity: float,
    session: AsyncSession,
) -> str | None:
    """Add one completed cycle to the task's progress. Returns any ETA revision trigger."""
    quantity = (task.completed_quantity or 0.0) + cycle_quantity(
        task.quantity_unit, cycle_payload_pct, bucket_capacity
    )
    return await update_progress(task.task_id, quantity, session)


async def update_progress(
    task_id: str,
    completed_quantity: float,
    session: AsyncSession,
) -> str | None:
    """Update completed_quantity from a telemetry cycle-completion tick.

    Also checks whether the ETA needs a mid-task revision. Returns the
    revision trigger, or None.
    """
    result = await session.execute(select(EdgeTask).where(EdgeTask.task_id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        return None
    task.completed_quantity = completed_quantity
    await session.flush()
    trigger = await revise_if_triggered(session, task)
    queue_task_status(session, task)
    return trigger
