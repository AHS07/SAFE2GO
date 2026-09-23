"""Shift summary for the operator, built live from edge data."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.assignment import EdgeMachine, EdgeOperator, EdgeShift, EdgeTask
from app.db.models.edge.behavior import BehaviorEvent
from app.db.models.edge.incident import Incident
from app.edge.shift_summary import build_shift_summary
from app.edge.training.recommendations import recommendations_for_shift
from app.schemas.shift_report import ReportTraining, ShiftReportResponse
from app.shared.enums import TaskStatus
from app.shared.shift_report import counts, critical_count, report_tasks, report_time


async def edge_shift_report(session: AsyncSession, shift: EdgeShift) -> ShiftReportResponse:
    operator = await session.get(EdgeOperator, shift.operator_id)
    machine = await session.get(EdgeMachine, shift.machine_id)
    tasks = (
        await session.execute(select(EdgeTask).where(EdgeTask.shift_id == shift.shift_id).order_by(EdgeTask.scheduled_start))
    ).scalars().all()
    incidents = (await session.execute(select(Incident).where(Incident.shift_id == shift.shift_id))).scalars().all()
    events = (
        await session.execute(select(BehaviorEvent.event_type).where(BehaviorEvent.shift_id == shift.shift_id))
    ).scalars().all()
    recs = await recommendations_for_shift(session, shift.operator_id, shift.shift_id)

    items = report_tasks(tasks)
    return ShiftReportResponse(
        shift_id=shift.shift_id,
        operator_name=operator.operator_name if operator else shift.operator_id,
        machine_model=machine.machine_model if machine else shift.machine_id,
        scheduled_start=shift.scheduled_start,
        scheduled_end=shift.scheduled_end,
        source="edge",
        tasks_done=sum(1 for t in items if t.status == TaskStatus.DONE.value),
        tasks_total=len(items),
        tasks=items,
        time=report_time(await build_shift_summary(session, shift.shift_id)),
        incidents_total=len(incidents),
        incidents_critical=critical_count(incidents),
        incidents=counts(i.incident_type for i in incidents),
        behavior=counts(events),
        training=[ReportTraining(module_id=m.module_id, title=m.title) for _, m in recs],
    )
