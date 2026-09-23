"""Shift summary for the admin, built from what the edge has synced to the cloud.

Time figures come from the synced shift_summary, so they appear once the
shift has ended on the machine. Recommended training is derived from the
incident and behavior types with the recommendation map, since the edge
keeps its own recommendation records.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models.cloud.analytics import ShiftSummary
from app.db.models.cloud.edge_records import CloudBehaviorEvent, CloudIncident
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.db.models.cloud.training import AnomalyTrainingMap, TrainingModule
from app.schemas.shift_report import ReportTraining, ShiftReportResponse
from app.shared.enums import TaskStatus
from app.shared.shift_report import counts, critical_count, report_tasks, report_time
from app.sync.fields import SHIFT_SUMMARY_FIELDS
from app.sync.outbox import row_dict


async def _mapped_training(session: AsyncSession, incident_types: set[str], event_types: set[str]) -> list[ReportTraining]:
    if not incident_types and not event_types:
        return []
    rows = (
        await session.execute(
            select(TrainingModule.module_id, TrainingModule.title, AnomalyTrainingMap.source_type, AnomalyTrainingMap.type_value)
            .join(TrainingModule, TrainingModule.module_id == AnomalyTrainingMap.recommended_module_id)
        )
    ).all()
    seen: dict[str, str] = {}
    for module_id, title, source_type, type_value in rows:
        wanted = incident_types if source_type == "incident" else event_types
        if type_value in wanted:
            seen.setdefault(module_id, title)
    return [ReportTraining(module_id=k, title=v) for k, v in seen.items()]


async def cloud_shift_report(session: AsyncSession, shift_id: str) -> ShiftReportResponse:
    shift = await session.get(Shift, shift_id)
    if shift is None:
        raise NotFoundError(f"Shift {shift_id} not found.", details={"shift_id": shift_id})
    operator = await session.get(Operator, shift.operator_id)
    machine = await session.get(Machine, shift.machine_id)
    tasks = (await session.execute(select(Task).where(Task.shift_id == shift_id).order_by(Task.scheduled_start))).scalars().all()
    incidents = (await session.execute(select(CloudIncident).where(CloudIncident.shift_id == shift_id))).scalars().all()
    events = (
        await session.execute(select(CloudBehaviorEvent.event_type).where(CloudBehaviorEvent.shift_id == shift_id))
    ).scalars().all()
    summary = await session.get(ShiftSummary, shift_id)

    items = report_tasks(tasks)
    return ShiftReportResponse(
        shift_id=shift.shift_id,
        operator_name=operator.operator_name if operator else shift.operator_id,
        machine_model=machine.machine_model if machine else shift.machine_id,
        scheduled_start=shift.scheduled_start,
        scheduled_end=shift.scheduled_end,
        source="cloud",
        tasks_done=sum(1 for t in items if t.status == TaskStatus.DONE.value),
        tasks_total=len(items),
        tasks=items,
        time=report_time(row_dict(summary, SHIFT_SUMMARY_FIELDS) if summary else None),
        incidents_total=len(incidents),
        incidents_critical=critical_count(incidents),
        incidents=counts(i.incident_type for i in incidents),
        behavior=counts(events),
        training=await _mapped_training(session, {i.incident_type for i in incidents}, set(events)),
    )
