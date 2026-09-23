"""Apply edge messages to the cloud schema.

Every handler is idempotent: records are inserted once by their edge id,
and updates are skipped when the row already holds a newer message.

Conflicts: the edge owns a task once it starts. If the cloud cancelled the
task, or moved it to another shift, before hearing that the edge had
started it, the cloud change is rolled back to the assignment the edge is
working to and a sync_conflict is recorded for the admin.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.maintenance import add_shift_hours
from app.cloud.publish import queue_machines
from app.core.clock import sim_now
from app.db.models.cloud.analytics import ShiftSummary, SyncConflict
from app.db.models.cloud.edge_records import CloudBehaviorEvent, CloudIncident
from app.db.models.cloud.task import Task
from app.db.models.cloud.training import QuizResult
from app.db.models.cloud.user import AuditLog
from app.shared.enums import AuditAction, ConflictType, SyncMessageType, Tier
from app.sync.fields import (
    BEHAVIOR_EVENT_FIELDS,
    INCIDENT_FIELDS,
    QUIZ_RESULT_FIELDS,
    SHIFT_SUMMARY_FIELDS,
    TASK_ASSIGNMENT_FIELDS,
    TASK_EDGE_FIELDS,
)
from app.sync.outbox import parse_dt, payload_values

log = logging.getLogger("safe2go.sync.edge_to_cloud")

Handler = Callable[[AsyncSession, dict, int], Awaitable[None]]


# ---------------------------------------------------------------------------
# Task status and conflicts
# ---------------------------------------------------------------------------


async def _cancelled_at(session: AsyncSession, task_id: str) -> datetime:
    """When the admin cancelled the task (from the audit log)."""
    ts = (
        await session.execute(
            select(AuditLog.timestamp)
            .where(AuditLog.action == AuditAction.TASK_CANCEL.value)
            .where(AuditLog.target_id == task_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return ts or sim_now()


def _conflict_type(task: Task, edge_shift_id: str) -> ConflictType | None:
    if task.status == "cancelled":
        return ConflictType.CANCEL_AFTER_START
    if task.shift_id != edge_shift_id:
        return ConflictType.REASSIGN_AFTER_START
    return None


async def _resolve_conflict(
    session: AsyncSession, task: Task, conflict: ConflictType, assignment: dict, edge_actual_start: datetime
) -> None:
    """Roll the cloud change back to the assignment the edge started."""
    cloud_changed_at = (
        await _cancelled_at(session, task.task_id)
        if conflict == ConflictType.CANCEL_AFTER_START
        else task.reassigned_at or sim_now()
    )
    session.add(SyncConflict(
        conflict_id=str(uuid.uuid4()),
        task_id=task.task_id,
        conflict_type=conflict.value,
        edge_actual_start=edge_actual_start,
        cloud_reassigned_at=cloud_changed_at,
        created_at=sim_now(),
        resolved=False,
    ))
    for name, value in assignment.items():
        setattr(task, name, value)
    log.warning(
        "Sync conflict: cloud change rolled back, task already started on the edge",
        extra={"task_id": task.task_id, "conflict_type": conflict.value},
    )


async def apply_task_status(session: AsyncSession, payload: dict, seq: int) -> None:
    task = await session.get(Task, payload["task_id"])
    if task is None:
        log.warning("Task status for unknown task skipped", extra={"task_id": payload["task_id"]})
        return
    if seq <= task.edge_sync_seq:
        return

    edge_values = payload_values(payload, TASK_EDGE_FIELDS)
    if edge_values["actual_start"] is not None:
        conflict = _conflict_type(task, payload["shift_id"])
        if conflict is not None:
            assignment = payload_values(payload, TASK_ASSIGNMENT_FIELDS)
            await _resolve_conflict(session, task, conflict, assignment, edge_values["actual_start"])

    for name, value in edge_values.items():
        setattr(task, name, value)
    task.edge_sync_seq = seq
    await session.flush()


# ---------------------------------------------------------------------------
# Incidents, behavior events, quiz results, summaries, audit
# ---------------------------------------------------------------------------


async def apply_incident(session: AsyncSession, payload: dict, seq: int) -> None:
    values = payload_values(payload, INCIDENT_FIELDS)
    incident = await session.get(CloudIncident, values["incident_id"])
    if incident is None:
        session.add(CloudIncident(**values, sync_seq=seq))
    elif seq > incident.sync_seq:
        for name, value in values.items():
            setattr(incident, name, value)
        incident.sync_seq = seq
    await session.flush()


async def _insert_once(session: AsyncSession, model: type, key: str, values: dict) -> None:
    if await session.get(model, key) is None:
        session.add(model(**values))
        await session.flush()


async def apply_behavior_event(session: AsyncSession, payload: dict, seq: int) -> None:
    values = payload_values(payload, BEHAVIOR_EVENT_FIELDS)
    await _insert_once(session, CloudBehaviorEvent, values["event_id"], values)


async def apply_quiz_result(session: AsyncSession, payload: dict, seq: int) -> None:
    values = payload_values(payload, QUIZ_RESULT_FIELDS)
    await _insert_once(session, QuizResult, values["result_id"], values)


async def apply_shift_summary(session: AsyncSession, payload: dict, seq: int) -> None:
    """Store the summary and move the machine's hour meter by the newly reported hours.

    The meter moves by the difference to any earlier summary of the same
    shift, so a repeated summary never counts its hours twice.
    """
    values = payload_values(payload, SHIFT_SUMMARY_FIELDS)
    summary = await session.get(ShiftSummary, values["shift_id"])
    previous_hours = summary.engine_hours if summary is not None else 0.0
    if summary is None:
        session.add(ShiftSummary(**values))
    else:
        for name, value in values.items():
            setattr(summary, name, value)
    await session.flush()
    if await add_shift_hours(session, values["shift_id"], values["engine_hours"] - previous_hours) is not None:
        # The edge starts its live meter from the machine list, so send the new reading back.
        await queue_machines(session)
    await session.flush()


async def apply_audit_record(session: AsyncSession, payload: dict, seq: int) -> None:
    await _insert_once(session, AuditLog, payload["log_id"], {
        "log_id": payload["log_id"],
        "user_id": payload["user_id"],
        "action": payload["action"],
        "target_entity": payload["target_entity"],
        "target_id": payload["target_id"],
        "timestamp": parse_dt(payload["timestamp"]),
        "tier": Tier.EDGE.value,
    })


HANDLERS: dict[str, Handler] = {
    SyncMessageType.TASK_STATUS.value: apply_task_status,
    SyncMessageType.INCIDENT.value: apply_incident,
    SyncMessageType.BEHAVIOR_EVENT.value: apply_behavior_event,
    SyncMessageType.QUIZ_RESULT.value: apply_quiz_result,
    SyncMessageType.SHIFT_SUMMARY.value: apply_shift_summary,
    SyncMessageType.AUDIT_RECORD.value: apply_audit_record,
}
