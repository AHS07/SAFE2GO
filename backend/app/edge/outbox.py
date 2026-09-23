"""Edge-to-cloud messages.

Queued in the same transaction as the edge change they report. They wait
in edge_outbox while the cloud is unreachable and flush in order on
reconnect.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.db.models.edge.assignment import EdgeTask
from app.db.models.edge.behavior import BehaviorEvent
from app.db.models.edge.incident import Incident
from app.db.models.edge.sync import EdgeOutbox
from app.db.models.edge.training import EdgeQuizResult
from app.shared.enums import AuditAction, SyncMessageType
from app.sync.fields import (
    BEHAVIOR_EVENT_FIELDS,
    INCIDENT_FIELDS,
    QUIZ_RESULT_FIELDS,
    TASK_ASSIGNMENT_FIELDS,
    TASK_EDGE_FIELDS,
)
from app.sync.outbox import enqueue, row_dict


def queue_task_status(session: AsyncSession, task: EdgeTask) -> None:
    """Edge-owned fields, plus the assignment the edge is working to.

    The cloud needs the assignment to restore it if it changed the task
    after the edge had already started it (sync conflict).
    """
    payload = {
        "task_id": task.task_id,
        **row_dict(task, TASK_EDGE_FIELDS),
        **row_dict(task, TASK_ASSIGNMENT_FIELDS),
    }
    enqueue(session, EdgeOutbox, SyncMessageType.TASK_STATUS, payload, task.task_id)


def queue_incident(session: AsyncSession, incident: Incident) -> None:
    enqueue(session, EdgeOutbox, SyncMessageType.INCIDENT, row_dict(incident, INCIDENT_FIELDS), incident.incident_id)


def queue_behavior_event(session: AsyncSession, event: BehaviorEvent) -> None:
    enqueue(
        session, EdgeOutbox, SyncMessageType.BEHAVIOR_EVENT, row_dict(event, BEHAVIOR_EVENT_FIELDS), event.event_id
    )


def queue_quiz_result(session: AsyncSession, result: EdgeQuizResult) -> None:
    enqueue(session, EdgeOutbox, SyncMessageType.QUIZ_RESULT, row_dict(result, QUIZ_RESULT_FIELDS), result.result_id)


def queue_shift_summary(session: AsyncSession, summary: dict) -> None:
    enqueue(session, EdgeOutbox, SyncMessageType.SHIFT_SUMMARY, summary, summary["shift_id"])


def queue_audit(
    session: AsyncSession,
    user_id: str | None,
    action: AuditAction,
    target_entity: str | None,
    target_id: str | None,
) -> None:
    """An edge action for the cloud audit log (tier = edge)."""
    log_id = str(uuid.uuid4())
    enqueue(session, EdgeOutbox, SyncMessageType.AUDIT_RECORD, {
        "log_id": log_id,
        "user_id": user_id,
        "action": action.value,
        "target_entity": target_entity,
        "target_id": target_id,
        "timestamp": sim_now(),
    }, log_id)
