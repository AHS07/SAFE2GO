"""Operator view builders shared by the REST routes and the WebSocket snapshot."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.assignment import EdgeMachine, EdgeOperator, EdgeShift, EdgeTask
from app.db.models.edge.behavior import BehaviorEvent
from app.db.models.edge.incident import Incident
from app.edge.behavior.coaching import coaching_message
from app.edge.telemetry.state import latest_tick
from app.edge.training.access import is_parked
from app.edge.training.recommendations import mapped_module
from app.schemas.operator import (
    CoachingResponse,
    IncidentResponse,
    MachineStatusResponse,
    ShiftResponse,
    TaskResponse,
)
from app.shared.enums import TaskStatus
from app.shared.eta_model import displayed_eta

_INCIDENT_LIMIT = 50


def task_view(task: EdgeTask) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        shift_id=task.shift_id,
        task_type=task.task_type,
        material_type=task.material_type,
        target_quantity=task.target_quantity,
        quantity_unit=task.quantity_unit,
        completed_quantity=task.completed_quantity or 0.0,
        status=task.status,
        scheduled_start=task.scheduled_start,
        scheduled_end=task.scheduled_end,
        eta_minutes=displayed_eta(task.planning_eta, task.revised_predicted_time),
        eta_from_history=task.is_fallback,
        eta_revision_reason=task.revision_reason,
        actual_start=task.actual_start,
        actual_end=task.actual_end,
    )


def incident_view(incident: Incident) -> IncidentResponse:
    return IncidentResponse(
        incident_id=incident.incident_id,
        incident_type=incident.incident_type,
        peak_severity=incident.peak_severity,
        status=incident.status,
        source=incident.source,
        event_start=incident.event_start,
        event_end=incident.event_end,
        escalation_reason=incident.escalation_reason,
        description=incident.description,
        acknowledged_at=incident.acknowledged_at,
    )


def machine_status_view(machine_id: str) -> MachineStatusResponse:
    tick = latest_tick(machine_id)
    if tick is None:
        return MachineStatusResponse(machine_id=machine_id, live=False, parked=False)
    return MachineStatusResponse(
        machine_id=machine_id,
        live=True,
        parked=is_parked(tick),
        timestamp=tick.timestamp,
        engine_running=tick.engine_running,
        engine_rpm=tick.engine_rpm,
        engine_hours=tick.engine_hours,
        fuel_used=tick.fuel_used,
        hydraulic_active=tick.hydraulic_active,
        machine_speed=tick.machine_speed,
        payload_pct=tick.payload_pct,
        seatbelt_status=tick.seatbelt_status,
        seat_occupied=tick.seat_occupied,
        park_brake=tick.park_brake,
        gear_state=tick.gear_state,
        proximity_distance=tick.proximity_distance,
        proximity_sensor_ok=tick.proximity_sensor_ok,
        ambient_temp=tick.ambient_temp,
        visibility=tick.visibility,
        tilt_angle=tick.tilt_angle,
        task_id=tick.task_id,
    )


async def shift_view(session: AsyncSession, shift: EdgeShift) -> ShiftResponse:
    machine = await session.get(EdgeMachine, shift.machine_id)
    operator = await session.get(EdgeOperator, shift.operator_id)
    return ShiftResponse(
        shift_id=shift.shift_id,
        machine_id=shift.machine_id,
        operator_id=shift.operator_id,
        operator_name=operator.operator_name if operator else shift.operator_id,
        scheduled_start=shift.scheduled_start,
        scheduled_end=shift.scheduled_end,
        weather_forecast=shift.weather_forecast,
        machine_model=machine.machine_model,
        machine_type=machine.machine_type,
        bucket_capacity=machine.bucket_capacity,
        tilt_limit_degrees=machine.tilt_limit_degrees,
        rated_max_rpm=machine.rated_max_rpm,
    )


async def shift_tasks(session: AsyncSession, shift_id: str) -> list[TaskResponse]:
    result = await session.execute(
        select(EdgeTask)
        .where(EdgeTask.shift_id == shift_id)
        .where(EdgeTask.status != TaskStatus.CANCELLED.value)
        .order_by(EdgeTask.scheduled_start)
    )
    return [task_view(t) for t in result.scalars().all()]


async def shift_incidents(session: AsyncSession, shift_id: str) -> list[IncidentResponse]:
    result = await session.execute(
        select(Incident)
        .where(Incident.shift_id == shift_id)
        .order_by(Incident.event_start.desc())
        .limit(_INCIDENT_LIMIT)
    )
    return [incident_view(i) for i in result.scalars().all()]


async def shift_coaching(session: AsyncSession, shift_id: str) -> list[CoachingResponse]:
    result = await session.execute(
        select(BehaviorEvent)
        .where(BehaviorEvent.shift_id == shift_id)
        .order_by(BehaviorEvent.start.desc())
    )
    views: list[CoachingResponse] = []
    for event in result.scalars().all():
        module = await mapped_module(session, "behavior", event.event_type)
        views.append(CoachingResponse(
            event_id=event.event_id,
            event_type=event.event_type,
            message=coaching_message(event.event_type, event.magnitude),
            magnitude=event.magnitude,
            baseline_value=event.baseline_value,
            start=event.start,
            end=event.end,
            module_id=module.module_id if module else None,
            module_title=module.title if module else None,
        ))
    return views
