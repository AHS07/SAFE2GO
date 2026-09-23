"""Cloud-to-edge messages.

Assignment changes are queued by the assignment service in the same
transaction as the change. Reference data (machines, operator roster,
baselines, ETA aggregates, training content) is queued as full snapshots;
the edge replaces its copy with each newer snapshot.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.analytics import BehaviorBaseline, EtaAggregate
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.sync import CloudOutbox
from app.db.models.cloud.task import Task
from app.db.models.cloud.training import AnomalyTrainingMap, TrainingModule
from app.db.models.cloud.user import OfflineCredential, User
from app.shared.enums import SyncMessageType, UserRole
from app.shared.eta_model import get_predictor
from app.sync.fields import (
    AGGREGATE_FIELDS,
    BASELINE_FIELDS,
    MACHINE_FIELDS,
    SHIFT_FIELDS,
    TASK_ASSIGNMENT_FIELDS,
)
from app.sync.outbox import enqueue, row_dict

log = logging.getLogger("safe2go.cloud_publish")

_MODULE_FIELDS = ("module_id", "title", "format", "duration_min", "content_path")
_MAP_FIELDS = ("map_id", "source_type", "type_value", "recommended_module_id")


# ---------------------------------------------------------------------------
# Assignment changes
# ---------------------------------------------------------------------------


def queue_shift(session: AsyncSession, shift: Shift) -> None:
    enqueue(session, CloudOutbox, SyncMessageType.SHIFT_RECORD, row_dict(shift, SHIFT_FIELDS), shift.shift_id)


def queue_task(session: AsyncSession, task: Task, message_type: SyncMessageType) -> None:
    """task_assigned or task_reassigned: the full assignment snapshot."""
    payload = {"task_id": task.task_id, **row_dict(task, TASK_ASSIGNMENT_FIELDS)}
    enqueue(session, CloudOutbox, message_type, payload, task.task_id)


def queue_task_cancelled(session: AsyncSession, task: Task) -> None:
    enqueue(session, CloudOutbox, SyncMessageType.TASK_CANCELLED, {"task_id": task.task_id}, task.task_id)


def queue_credential(session: AsyncSession, credential: OfflineCredential) -> None:
    payload = {
        "credential_id": credential.credential_id,
        "shift_id": credential.shift_id,
        "operator_id": credential.operator_id,
        "machine_id": credential.machine_id,
        "expires_at": credential.expires_at,
        "signature": credential.signature,
    }
    enqueue(session, CloudOutbox, SyncMessageType.OFFLINE_CREDENTIAL, payload, credential.shift_id)


# ---------------------------------------------------------------------------
# Reference snapshots
# ---------------------------------------------------------------------------


async def queue_machines(session: AsyncSession) -> None:
    machines = (await session.execute(select(Machine))).scalars().all()
    enqueue(session, CloudOutbox, SyncMessageType.MACHINE_MASTER,
            {"machines": [row_dict(m, MACHINE_FIELDS) for m in machines]})


async def queue_operator_roster(session: AsyncSession) -> None:
    """Operators with qualifications, plus username and user_id for offline login."""
    operators = (await session.execute(select(Operator))).scalars().all()
    quals = (await session.execute(select(OperatorQualification))).scalars().all()
    users = (
        await session.execute(
            select(User.user_id, User.username, User.linked_operator_id)
            .where(User.role == UserRole.OPERATOR.value)
            .where(User.linked_operator_id.is_not(None))
        )
    ).all()
    by_operator = {u.linked_operator_id: u for u in users}
    quals_by_operator: dict[str, list[dict]] = {}
    for q in quals:
        quals_by_operator.setdefault(q.operator_id, []).append(
            {"machine_type": q.machine_type, "skill_level": q.skill_level}
        )
    roster = [
        {
            "operator_id": op.operator_id,
            "operator_name": op.operator_name,
            "user_id": by_operator[op.operator_id].user_id if op.operator_id in by_operator else None,
            "username": by_operator[op.operator_id].username if op.operator_id in by_operator else None,
            "qualifications": quals_by_operator.get(op.operator_id, []),
        }
        for op in operators
    ]
    enqueue(session, CloudOutbox, SyncMessageType.OPERATOR_ROSTER, {"operators": roster})


async def queue_baselines(session: AsyncSession) -> None:
    rows = (await session.execute(select(BehaviorBaseline))).scalars().all()
    enqueue(session, CloudOutbox, SyncMessageType.BEHAVIOR_BASELINE,
            {"rows": [row_dict(r, BASELINE_FIELDS) for r in rows]})


async def queue_eta_reference(session: AsyncSession) -> None:
    """Model version in use plus the deployed aggregates. The model file itself is shared."""
    rows = (await session.execute(select(EtaAggregate))).scalars().all()
    enqueue(session, CloudOutbox, SyncMessageType.ETA_MODEL_REF, {
        "model_version": get_predictor().model_version,
        "aggregates_version": rows[0].version if rows else None,
        "rows": [row_dict(r, AGGREGATE_FIELDS) for r in rows],
    })


async def queue_training_content(session: AsyncSession) -> None:
    modules = (await session.execute(select(TrainingModule))).scalars().all()
    entries = (await session.execute(select(AnomalyTrainingMap))).scalars().all()
    enqueue(session, CloudOutbox, SyncMessageType.TRAINING_CONTENT, {
        "modules": [row_dict(m, _MODULE_FIELDS) for m in modules],
        "map": [row_dict(e, _MAP_FIELDS) for e in entries],
    })


async def queue_reference_snapshots(session: AsyncSession) -> None:
    await queue_machines(session)
    await queue_operator_roster(session)
    await queue_baselines(session)
    await queue_eta_reference(session)
    await queue_training_content(session)
    log.info("Reference snapshots queued for the edge")


async def queue_operator_shifts(session: AsyncSession, operator_ids: list[str]) -> int:
    """Queue every shift, task, and credential of these operators (edge bootstrap)."""
    shifts = (await session.execute(select(Shift).where(Shift.operator_id.in_(operator_ids)))).scalars().all()
    for shift in shifts:
        queue_shift(session, shift)
        tasks = (await session.execute(select(Task).where(Task.shift_id == shift.shift_id))).scalars().all()
        for task in tasks:
            queue_task(session, task, SyncMessageType.TASK_ASSIGNED)
            if task.status == "cancelled":
                queue_task_cancelled(session, task)
        credential = (
            await session.execute(select(OfflineCredential).where(OfflineCredential.shift_id == shift.shift_id))
        ).scalar_one_or_none()
        if credential is not None:
            queue_credential(session, credential)
    return len(shifts)
