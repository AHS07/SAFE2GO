"""Operator REST routes (edge tier).

All routes require an operator token and act only on the operator's own
current shift.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operator_views import (
    incident_view,
    machine_status_view,
    shift_coaching,
    shift_incidents,
    shift_tasks,
    shift_view,
    task_view,
)
from app.api.ws.manager import ws_manager
from app.core.clock import sim_now
from app.core.deps import require_operator
from app.core.errors import ForbiddenError, NotFoundError
from app.db.models.edge.assignment import EdgeShift
from app.db.models.edge.incident import Incident
from app.db.session import get_db
from app.edge.maintenance import machine_service
from app.edge.outbox import queue_audit, queue_incident
from app.edge.safety.acknowledgement import acknowledge
from app.edge.shift import current_shift_for_operator
from app.edge.shift_report import edge_shift_report
from app.edge.tasks.service import apply_action, get_task
from app.edge.telemetry.state import latest_tick
from app.edge.training.recommendations import recommendations_for_shift
from app.edge.training.service import TrainingStatus, statuses_for_operator
from app.schemas.maintenance import ServiceSuggestionResponse
from app.schemas.operator import (
    CoachingResponse,
    IncidentResponse,
    MachineStatusResponse,
    ManualIncidentRequest,
    ShiftResponse,
    TaskActionRequest,
    TaskResponse,
)
from app.schemas.shift_report import ShiftReportResponse
from app.schemas.training import RecommendationResponse
from app.shared.enums import AuditAction, IncidentStatus, IncidentType, Severity

log = logging.getLogger("safe2go.operator_routes")
router = APIRouter(prefix="/api/operator", tags=["operator"])


def operator_id_of(user: dict) -> str:
    return user.get("operator_id") or user["sub"]


async def _require_shift(session: AsyncSession, operator_id: str) -> EdgeShift:
    shift = await current_shift_for_operator(session, operator_id)
    if shift is None:
        raise NotFoundError("No current shift found for this operator.")
    return shift


# ---------------------------------------------------------------------------
# Shift, machine status, tasks
# ---------------------------------------------------------------------------


@router.get("/shift/current", response_model=ShiftResponse)
async def get_current_shift(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> ShiftResponse:
    shift = await _require_shift(session, operator_id_of(user))
    return await shift_view(session, shift)


@router.get("/shift/summary", response_model=ShiftReportResponse)
async def get_shift_summary(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> ShiftReportResponse:
    """The current shift so far: tasks, working and idle time, incidents, coaching, training."""
    return await edge_shift_report(session, await _require_shift(session, operator_id_of(user)))


@router.get("/status", response_model=MachineStatusResponse)
async def get_machine_status(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> MachineStatusResponse:
    shift = await _require_shift(session, operator_id_of(user))
    return machine_status_view(shift.machine_id)


@router.get("/maintenance", response_model=ServiceSuggestionResponse)
async def get_maintenance(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> ServiceSuggestionResponse:
    shift = await _require_shift(session, operator_id_of(user))
    return ServiceSuggestionResponse(**asdict(await machine_service(session, shift.machine_id)))


@router.get("/tasks", response_model=list[TaskResponse])
async def get_tasks(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    shift = await current_shift_for_operator(session, operator_id_of(user))
    return [] if shift is None else await shift_tasks(session, shift.shift_id)


@router.post("/tasks/{task_id}/{action}", response_model=TaskResponse)
async def task_action(
    task_id: str = Path(...),
    action: str = Path(..., pattern="^(start|pause|resume|block|unblock|complete)$"),
    body: TaskActionRequest | None = None,
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> TaskResponse:
    shift = await _require_shift(session, operator_id_of(user))
    task = await get_task(task_id, session)
    if task.shift_id != shift.shift_id:
        raise ForbiddenError("This task is not in your current shift.")

    task = await apply_action(task_id, action, session, actor_user_id=user.get("sub"))
    await session.commit()

    view = task_view(task)
    await ws_manager.broadcast(shift.machine_id, {
        "type": "progress",
        "data": {
            "task_id": view.task_id,
            "status": view.status,
            "completed_quantity": view.completed_quantity,
            "target_quantity": view.target_quantity,
            "target_reached": view.completed_quantity >= view.target_quantity,
        },
    })
    return view


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@router.get("/incidents", response_model=list[IncidentResponse])
async def get_incidents(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> list[IncidentResponse]:
    shift = await current_shift_for_operator(session, operator_id_of(user))
    return [] if shift is None else await shift_incidents(session, shift.shift_id)


@router.post("/incidents", response_model=IncidentResponse, status_code=201)
async def report_incident(
    body: ManualIncidentRequest,
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> IncidentResponse:
    operator_id = operator_id_of(user)
    shift = await _require_shift(session, operator_id)
    tick = latest_tick(shift.machine_id)
    now = sim_now()

    # A report describes something that already happened, so it has an end
    # time and resolves once acknowledged.
    incident = Incident(
        incident_id=str(uuid.uuid4()),
        shift_id=shift.shift_id,
        machine_id=shift.machine_id,
        operator_id=operator_id,
        task_id=tick.task_id if tick else None,
        event_start=now,
        event_end=now,
        incident_type=IncidentType.MANUAL_REPORT.value,
        peak_severity=Severity.WARNING.value,
        status=IncidentStatus.OPEN.value,
        source="operator",
        description=body.description.strip(),
    )
    session.add(incident)
    await session.flush()
    queue_incident(session, incident)
    queue_audit(session, user.get("sub"), AuditAction.INCIDENT_REPORT, "incident", incident.incident_id)
    await session.commit()

    view = incident_view(incident)
    await _broadcast_incident(shift.machine_id, view, "reported")
    return view


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentResponse)
async def acknowledge_incident(
    incident_id: str = Path(...),
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> IncidentResponse:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(f"Incident {incident_id} not found.")

    acknowledge(incident, latest_tick(incident.machine_id), operator_id_of(user), sim_now())
    await session.flush()
    queue_incident(session, incident)
    queue_audit(session, user.get("sub"), AuditAction.INCIDENT_ACKNOWLEDGE, "incident", incident.incident_id)
    await session.commit()

    view = incident_view(incident)
    await _broadcast_incident(incident.machine_id, view, "acknowledged")
    return view


async def _broadcast_incident(machine_id: str, view: IncidentResponse, event: str) -> None:
    await ws_manager.broadcast(machine_id, {
        "type": "incident",
        "data": {
            "incident_id": view.incident_id,
            "incident_type": view.incident_type,
            "severity": view.peak_severity,
            "status": view.status,
            "event": event,
        },
    })


# ---------------------------------------------------------------------------
# Coaching and training recommendations
# ---------------------------------------------------------------------------


@router.get("/coaching", response_model=list[CoachingResponse])
async def get_coaching(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> list[CoachingResponse]:
    shift = await current_shift_for_operator(session, operator_id_of(user))
    return [] if shift is None else await shift_coaching(session, shift.shift_id)


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> list[RecommendationResponse]:
    operator_id = operator_id_of(user)
    shift = await current_shift_for_operator(session, operator_id)
    if shift is None:
        return []

    recs = await recommendations_for_shift(session, operator_id, shift.shift_id)
    statuses = await statuses_for_operator(session, operator_id)
    return [
        RecommendationResponse(
            recommendation_id=rec.recommendation_id,
            module_id=module.module_id,
            module_title=module.title,
            source_type=rec.source_type,
            created_at=rec.created_at,
            status=statuses.get(module.module_id, TrainingStatus.NOT_STARTED),
        )
        for rec, module in recs
    ]
