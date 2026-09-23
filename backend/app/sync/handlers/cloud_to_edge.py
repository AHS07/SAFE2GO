"""Apply cloud messages to the edge schema.

Every handler is idempotent. Snapshots replace the edge copy and are
skipped when a newer snapshot of the same type was already applied. Row
messages carry the outbox sequence and are skipped when the row already
holds a newer one, so late or repeated messages never undo newer data.

Once a task has started, the edge owns it: assignment changes for a
started task are ignored here, and the cloud detects the conflict when the
edge reports the start.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.assignment import (
    EdgeBehaviorBaseline,
    EdgeEtaAggregate,
    EdgeMachine,
    EdgeOfflineCredential,
    EdgeOperator,
    EdgeOperatorQualification,
    EdgeShift,
    EdgeTask,
)
from app.db.models.edge.sync import EdgeInbox
from app.db.models.edge.training import EdgeAnomalyTrainingMap, EdgeTrainingModule
from app.shared.enums import SyncMessageType, TaskStatus
from app.shared.eta_model import get_predictor
from app.sync.fields import (
    AGGREGATE_FIELDS,
    BASELINE_FIELDS,
    MACHINE_FIELDS,
    SHIFT_FIELDS,
    TASK_ASSIGNMENT_FIELDS,
)
from app.sync.outbox import parse_dt, payload_values

log = logging.getLogger("safe2go.sync.cloud_to_edge")

Handler = Callable[[AsyncSession, dict, int], Awaitable[None]]


async def _newer_snapshot_applied(session: AsyncSession, message_type: SyncMessageType, seq: int) -> bool:
    latest = (
        await session.execute(
            select(func.max(EdgeInbox.seq)).where(EdgeInbox.message_type == message_type.value)
        )
    ).scalar_one()
    return latest is not None and latest > seq


async def _replace(session: AsyncSession, model: type, rows: list[dict]) -> None:
    await session.execute(delete(model))
    session.add_all([model(**row) for row in rows])
    await session.flush()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


async def apply_machine_master(session: AsyncSession, payload: dict, seq: int) -> None:
    if await _newer_snapshot_applied(session, SyncMessageType.MACHINE_MASTER, seq):
        return
    await _replace(session, EdgeMachine, [payload_values(m, MACHINE_FIELDS) for m in payload["machines"]])


async def apply_operator_roster(session: AsyncSession, payload: dict, seq: int) -> None:
    if await _newer_snapshot_applied(session, SyncMessageType.OPERATOR_ROSTER, seq):
        return
    await session.execute(delete(EdgeOperatorQualification))
    await _replace(session, EdgeOperator, [
        {k: op[k] for k in ("operator_id", "operator_name", "user_id", "username")}
        for op in payload["operators"]
    ])
    session.add_all([
        EdgeOperatorQualification(operator_id=op["operator_id"], **q)
        for op in payload["operators"]
        for q in op["qualifications"]
    ])
    await session.flush()


async def apply_behavior_baseline(session: AsyncSession, payload: dict, seq: int) -> None:
    if await _newer_snapshot_applied(session, SyncMessageType.BEHAVIOR_BASELINE, seq):
        return
    await _replace(session, EdgeBehaviorBaseline, [payload_values(r, BASELINE_FIELDS) for r in payload["rows"]])


async def apply_eta_model_ref(session: AsyncSession, payload: dict, seq: int) -> None:
    if await _newer_snapshot_applied(session, SyncMessageType.ETA_MODEL_REF, seq):
        return
    await _replace(session, EdgeEtaAggregate, [payload_values(r, AGGREGATE_FIELDS) for r in payload["rows"]])
    loaded = get_predictor().model_version
    if loaded != payload["model_version"]:
        # Revisions still work; they are stamped with the model the edge actually has.
        log.warning(
            "Edge model differs from the cloud model",
            extra={"edge_model_version": loaded, "cloud_model_version": payload["model_version"]},
        )


async def apply_training_content(session: AsyncSession, payload: dict, seq: int) -> None:
    if await _newer_snapshot_applied(session, SyncMessageType.TRAINING_CONTENT, seq):
        return
    await session.execute(delete(EdgeAnomalyTrainingMap))
    await _replace(session, EdgeTrainingModule, payload["modules"])
    session.add_all([EdgeAnomalyTrainingMap(**entry) for entry in payload["map"]])
    await session.flush()


# ---------------------------------------------------------------------------
# Shifts, tasks, credentials
# ---------------------------------------------------------------------------


async def apply_shift_record(session: AsyncSession, payload: dict, seq: int) -> None:
    values = payload_values(payload, SHIFT_FIELDS)
    shift = await session.get(EdgeShift, values["shift_id"])
    if shift is None:
        session.add(EdgeShift(**values, sync_seq=seq))
    elif seq > shift.sync_seq:
        for name, value in values.items():
            setattr(shift, name, value)
        shift.sync_seq = seq
    await session.flush()


def _started(task: EdgeTask) -> bool:
    return task.actual_start is not None or task.status != TaskStatus.ASSIGNED.value


async def apply_task_assignment(session: AsyncSession, payload: dict, seq: int) -> None:
    """task_assigned and task_reassigned: the cloud's full assignment snapshot."""
    values = payload_values(payload, TASK_ASSIGNMENT_FIELDS)
    task = await session.get(EdgeTask, payload["task_id"])
    if task is None:
        session.add(EdgeTask(
            task_id=payload["task_id"],
            **values,
            status=TaskStatus.ASSIGNED.value,
            completed_quantity=0.0,
            paused_minutes=0.0,
            blocked_minutes=0.0,
            sync_seq=seq,
        ))
    elif seq <= task.sync_seq:
        return
    elif _started(task):
        log.warning("Assignment change ignored, task already started on the edge", extra={"task_id": task.task_id})
        return
    else:
        for name, value in values.items():
            setattr(task, name, value)
        task.sync_seq = seq
    await session.flush()


async def apply_task_cancelled(session: AsyncSession, payload: dict, seq: int) -> None:
    task = await session.get(EdgeTask, payload["task_id"])
    if task is None or seq <= task.sync_seq:
        return
    if _started(task):
        log.warning("Cancellation ignored, task already started on the edge", extra={"task_id": task.task_id})
        return
    task.status = TaskStatus.CANCELLED.value
    task.sync_seq = seq
    await session.flush()


async def apply_offline_credential(session: AsyncSession, payload: dict, seq: int) -> None:
    await session.execute(delete(EdgeOfflineCredential).where(EdgeOfflineCredential.shift_id == payload["shift_id"]))
    session.add(EdgeOfflineCredential(
        credential_id=payload["credential_id"],
        shift_id=payload["shift_id"],
        operator_id=payload["operator_id"],
        machine_id=payload["machine_id"],
        expires_at=parse_dt(payload["expires_at"]),
        signature=payload["signature"],
    ))
    await session.flush()


HANDLERS: dict[str, Handler] = {
    SyncMessageType.MACHINE_MASTER.value: apply_machine_master,
    SyncMessageType.OPERATOR_ROSTER.value: apply_operator_roster,
    SyncMessageType.BEHAVIOR_BASELINE.value: apply_behavior_baseline,
    SyncMessageType.ETA_MODEL_REF.value: apply_eta_model_ref,
    SyncMessageType.TRAINING_CONTENT.value: apply_training_content,
    SyncMessageType.SHIFT_RECORD.value: apply_shift_record,
    SyncMessageType.TASK_ASSIGNED.value: apply_task_assignment,
    SyncMessageType.TASK_REASSIGNED.value: apply_task_assignment,
    SyncMessageType.TASK_CANCELLED.value: apply_task_cancelled,
    SyncMessageType.OFFLINE_CREDENTIAL.value: apply_offline_credential,
}
