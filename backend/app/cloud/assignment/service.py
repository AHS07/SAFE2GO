"""Cloud assignment service: shifts, tasks, cancellation, reassignment.

The cloud owns a task until it starts. Every write here runs the
validation rules, computes the assignment-time ETA, derives scheduled_end,
and adds an audit entry and the outbox message for the edge in the same
transaction. The caller commits.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.assignment.validation import (
    AssignmentWarning,
    Slot,
    require_available,
    require_compatible,
    require_no_shift_overlap,
    require_no_task_overlap,
    require_qualification,
    require_start_inside_shift,
    require_unit_for_task,
    require_valid_window,
    shift_end_warning,
    sign_in_warning,
)
from app.cloud.audit.service import record_audit
from app.cloud.credentials.issuer import issue_for_shift
from app.cloud.eta.service import estimate_at_assignment
from app.cloud.publish import queue_credential, queue_shift, queue_task, queue_task_cancelled
from app.core.clock import sim_now
from app.core.errors import (
    InvalidStatusTransitionError,
    NotFoundError,
    TaskAlreadyStartedError,
    ValidationError,
)
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.sync import CloudOutbox
from app.db.models.cloud.task import Task
from app.db.models.cloud.user import User
from app.shared.enums import AuditAction, SyncMessageType, TaskStatus

log = logging.getLogger("safe2go.assignment")


@dataclass
class AssignmentResult:
    task: Task
    warnings: list[AssignmentWarning] = field(default_factory=list)


@dataclass
class ShiftResult:
    shift: Shift
    warnings: list[AssignmentWarning] = field(default_factory=list)


@dataclass(frozen=True)
class TaskDraft:
    shift_id: str
    task_type: str
    target_quantity: float
    quantity_unit: str
    material_type: str
    scheduled_start: datetime | None = None


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


async def _get(session: AsyncSession, model: type, key: str, label: str):  # noqa: ANN202 - returns the model instance
    row = await session.get(model, key)
    if row is None:
        raise NotFoundError(f"{label} {key} not found.", details={f"{label.lower()}_id": key})
    return row


async def _qualifications(session: AsyncSession, operator_id: str) -> dict[str, str]:
    result = await session.execute(
        select(OperatorQualification).where(OperatorQualification.operator_id == operator_id)
    )
    return {q.machine_type: q.skill_level for q in result.scalars().all()}


async def _shift_slots(session: AsyncSession, column, value: str, start: datetime, end: datetime) -> list[Slot]:  # noqa: ANN001 - SQLAlchemy column
    result = await session.execute(
        select(Shift.shift_id, Shift.scheduled_start, Shift.scheduled_end)
        .where(column == value)
        .where(Shift.scheduled_start < end)
        .where(Shift.scheduled_end > start)
    )
    return [Slot(r.shift_id, r.scheduled_start, r.scheduled_end) for r in result.all()]


async def _active_tasks(session: AsyncSession, shift_id: str, exclude_task_id: str | None = None) -> list[Task]:
    query = (
        select(Task)
        .where(Task.shift_id == shift_id)
        .where(Task.status != TaskStatus.CANCELLED.value)
        .order_by(Task.scheduled_start)
    )
    if exclude_task_id is not None:
        query = query.where(Task.task_id != exclude_task_id)
    result = await session.execute(query)
    return list(result.scalars().all())


def _task_slot(task: Task) -> Slot:
    return Slot(task.task_id, task.scheduled_start, task.scheduled_end or task.scheduled_start)


def _next_free_start(shift: Shift, tasks: list[Task]) -> datetime:
    """Default task start: right after the last planned task, or at shift start."""
    ends = [t.scheduled_end or t.scheduled_start for t in tasks]
    return max([shift.scheduled_start, *ends])


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


async def create_shift(
    session: AsyncSession,
    *,
    operator_id: str,
    machine_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    weather_forecast: str,
    actor_id: str | None,
) -> ShiftResult:
    """Validate and create a shift: one operator on one machine."""
    require_valid_window(scheduled_start, scheduled_end)
    await _get(session, Operator, operator_id, "Operator")
    machine: Machine = await _get(session, Machine, machine_id, "Machine")

    require_qualification(await _qualifications(session, operator_id), machine.machine_type, operator_id)
    require_available(machine.machine_status, machine_id)
    require_no_shift_overlap(
        scheduled_start,
        scheduled_end,
        operator_id,
        machine_id,
        await _shift_slots(session, Shift.operator_id, operator_id, scheduled_start, scheduled_end),
        await _shift_slots(session, Shift.machine_id, machine_id, scheduled_start, scheduled_end),
    )

    shift = Shift(
        shift_id=str(uuid.uuid4()),
        machine_id=machine_id,
        operator_id=operator_id,
        date=scheduled_start.replace(hour=0, minute=0, second=0, microsecond=0),
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        weather_forecast=weather_forecast,
        weather_actual=None,
    )
    session.add(shift)
    await session.flush()
    queue_shift(session, shift)
    credential = await issue_for_shift(session, shift)
    warnings: list[AssignmentWarning] = []
    if credential is not None:
        queue_credential(session, credential)
    else:
        has_account = (
            await session.execute(select(User.user_id).where(User.linked_operator_id == operator_id).limit(1))
        ).scalar_one_or_none() is not None
        warnings.append(sign_in_warning(operator_id, has_account))
    record_audit(session, actor_id, AuditAction.SHIFT_CREATE, "shift", shift.shift_id)
    log.info(
        "Shift created",
        extra={"shift_id": shift.shift_id, "machine_id": machine_id, "warnings": [w.code for w in warnings]},
    )
    return ShiftResult(shift, warnings)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


async def _place_task(
    session: AsyncSession,
    task: Task,
    shift: Shift,
    requested_start: datetime | None,
    *,
    reassignment: bool,
) -> list[AssignmentWarning]:
    """Run every task rule against the target shift, then compute the ETA.

    Sets shift_id, skill snapshot, scheduled_start, the estimate, and the
    derived scheduled_end on the task. Raises before any of it is committed.
    """
    machine: Machine = await _get(session, Machine, shift.machine_id, "Machine")
    skill = require_qualification(
        await _qualifications(session, shift.operator_id), machine.machine_type, shift.operator_id
    )
    require_compatible(task.task_type, machine.machine_type, machine.machine_id)
    require_available(machine.machine_status, machine.machine_id)

    others = await _active_tasks(session, shift.shift_id, exclude_task_id=task.task_id)
    start = requested_start or _next_free_start(shift, others)
    shift_slot = Slot(shift.shift_id, shift.scheduled_start, shift.scheduled_end)
    require_start_inside_shift(start, shift_slot)

    task.shift_id = shift.shift_id
    task.operator_skill_at_assignment = skill
    task.scheduled_start = start
    await estimate_at_assignment(session, task, reassignment=reassignment)

    require_no_task_overlap(start, task.scheduled_end, [_task_slot(t) for t in others])
    warning = shift_end_warning(task.scheduled_end, shift.scheduled_end)
    return [warning] if warning else []


async def create_task(session: AsyncSession, draft: TaskDraft, *, actor_id: str | None) -> AssignmentResult:
    """Validate and assign a new task to a shift."""
    require_unit_for_task(draft.task_type, draft.quantity_unit)
    shift: Shift = await _get(session, Shift, draft.shift_id, "Shift")
    task = Task(
        task_id=str(uuid.uuid4()),
        shift_id=shift.shift_id,
        task_type=draft.task_type,
        target_quantity=draft.target_quantity,
        quantity_unit=draft.quantity_unit,
        material_type=draft.material_type,
        completed_quantity=0.0,
        status=TaskStatus.ASSIGNED.value,
        paused_minutes=0.0,
        blocked_minutes=0.0,
        is_fallback=False,
    )
    # The task is added only after every rule passes, so a rejected
    # request never leaves a row behind.
    warnings = await _place_task(session, task, shift, draft.scheduled_start, reassignment=False)
    session.add(task)
    await session.flush()
    queue_task(session, task, SyncMessageType.TASK_ASSIGNED)
    record_audit(session, actor_id, AuditAction.TASK_CREATE, "task", task.task_id)
    log.info(
        "Task assigned",
        extra={"task_id": task.task_id, "shift_id": shift.shift_id, "warnings": [w.code for w in warnings]},
    )
    return AssignmentResult(task, warnings)


async def _get_unstarted_task(session: AsyncSession, task_id: str, action: str) -> Task:
    task: Task = await _get(session, Task, task_id, "Task")
    if task.status == TaskStatus.CANCELLED.value:
        raise InvalidStatusTransitionError(
            f"Task {task_id} is already cancelled.",
            details={"task_id": task_id, "current_status": task.status, "action": action},
        )
    if task.actual_start is not None or task.status != TaskStatus.ASSIGNED.value:
        raise TaskAlreadyStartedError(
            f"Task {task_id} has already started. Only tasks that have not started can be changed.",
            details={"task_id": task_id, "current_status": task.status, "action": action},
        )
    return task


async def cancel_task(session: AsyncSession, task_id: str, *, actor_id: str | None) -> Task:
    """Cancel a task that has not started."""
    task = await _get_unstarted_task(session, task_id, "cancel")
    task.status = TaskStatus.CANCELLED.value
    await session.flush()
    queue_task_cancelled(session, task)
    record_audit(session, actor_id, AuditAction.TASK_CANCEL, "task", task.task_id)
    log.info("Task cancelled", extra={"task_id": task.task_id})
    return task


async def reassign_task(
    session: AsyncSession,
    task_id: str,
    *,
    target_shift_id: str,
    scheduled_start: datetime | None,
    actor_id: str | None,
) -> AssignmentResult:
    """Move a task that has not started to another shift.

    Counts as a new assignment: the skill snapshot, the ETA (including
    raw_predicted_time) and scheduled_end are recomputed for the new shift.
    """
    task = await _get_unstarted_task(session, task_id, "reassign")
    if task.shift_id == target_shift_id:
        raise ValidationError(
            "The task is already on this shift.",
            details={"task_id": task_id, "target_shift_id": target_shift_id},
        )
    shift: Shift = await _get(session, Shift, target_shift_id, "Shift")
    from_shift_id = task.shift_id

    warnings = await _place_task(session, task, shift, scheduled_start, reassignment=True)
    task.reassigned_at = sim_now()
    await session.flush()
    queue_task(session, task, SyncMessageType.TASK_REASSIGNED)
    record_audit(session, actor_id, AuditAction.TASK_REASSIGN, "task", task.task_id)
    log.info(
        "Task reassigned",
        extra={"task_id": task.task_id, "from_shift_id": from_shift_id, "to_shift_id": shift.shift_id},
    )
    return AssignmentResult(task, warnings)


# ---------------------------------------------------------------------------
# Read models for the admin API
# ---------------------------------------------------------------------------


async def list_operators(session: AsyncSession) -> list[tuple[Operator, list[OperatorQualification]]]:
    operators = (await session.execute(select(Operator).order_by(Operator.operator_name))).scalars().all()
    quals = (await session.execute(select(OperatorQualification))).scalars().all()
    by_operator: dict[str, list[OperatorQualification]] = {}
    for q in quals:
        by_operator.setdefault(q.operator_id, []).append(q)
    return [(op, sorted(by_operator.get(op.operator_id, []), key=lambda q: q.machine_type)) for op in operators]


async def list_machines(session: AsyncSession) -> list[Machine]:
    result = await session.execute(select(Machine).order_by(Machine.machine_type, Machine.machine_model))
    return list(result.scalars().all())


async def list_shifts(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime | None,
    operator_id: str | None,
    machine_id: str | None,
    limit: int,
) -> list[Shift]:
    query = select(Shift).order_by(Shift.scheduled_start.desc()).limit(limit)
    if start is not None:
        query = query.where(Shift.scheduled_end > start)
    if end is not None:
        query = query.where(Shift.scheduled_start < end)
    if operator_id is not None:
        query = query.where(Shift.operator_id == operator_id)
    if machine_id is not None:
        query = query.where(Shift.machine_id == machine_id)
    return list((await session.execute(query)).scalars().all())


async def queued_entities(session: AsyncSession, entity_ids: list[str]) -> set[str]:
    """Ids with a cloud-to-edge message still waiting in the outbox."""
    if not entity_ids:
        return set()
    result = await session.execute(
        select(CloudOutbox.entity_id)
        .where(CloudOutbox.entity_id.in_(entity_ids))
        .where(CloudOutbox.delivered_at.is_(None))
    )
    return set(result.scalars().all())


async def list_shift_tasks(session: AsyncSession, shift_id: str) -> list[Task]:
    await _get(session, Shift, shift_id, "Shift")
    result = await session.execute(select(Task).where(Task.shift_id == shift_id).order_by(Task.scheduled_start))
    return list(result.scalars().all())
