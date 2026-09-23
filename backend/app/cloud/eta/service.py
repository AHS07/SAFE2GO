"""Cloud ETA service: assignment-time estimate and admin breakdown.

At assignment the cloud computes raw_predicted_time, planning_eta
(raw + buffer) and the derived scheduled_end, stamped with model_version and
aggregates_version. raw_predicted_time is written once and never changed;
edge revisions go to revised_predicted_time.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.db.models.cloud.analytics import EtaAggregate
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.shared.eta_aggregates import (
    AggregateRow,
    AggregateTable,
    aggregates_version,
    load_aggregate_table,
)
from app.shared.eta_model import (
    EtaEstimate,
    EtaInput,
    EtaPredictor,
    UnusualFeature,
    buffer_minutes,
    displayed_eta,
    get_predictor,
)

log = logging.getLogger("safe2go.cloud_eta")


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


async def replace_aggregates(session: AsyncSession, rows: list[AggregateRow]) -> str:
    """Replace the deployed aggregates and return their version."""
    version = aggregates_version(rows)
    await session.execute(delete(EtaAggregate))
    session.add_all([
        EtaAggregate(
            aggregate_id=str(uuid.uuid4()),
            scope=r.scope,
            scope_id=r.scope_id,
            task_type=r.task_type,
            avg_minutes_per_unit=r.avg_minutes_per_unit,
            avg_idle_ratio=r.avg_idle_ratio,
            avg_cycle_rate=r.avg_cycle_rate,
            version=version,
        )
        for r in rows
    ])
    await session.flush()
    log.info("ETA aggregates replaced", extra={"rows": len(rows), "aggregates_version": version})
    return version


# ---------------------------------------------------------------------------
# Assignment-time estimate
# ---------------------------------------------------------------------------


async def _load_context(session: AsyncSession, task: Task) -> tuple[Shift, Machine]:
    result = await session.execute(
        select(Shift, Machine)
        .join(Machine, Shift.machine_id == Machine.machine_id)
        .where(Shift.shift_id == task.shift_id)
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError(f"Shift {task.shift_id} for task {task.task_id} not found.")
    return row[0], row[1]


def assignment_input(task: Task, shift: Shift, machine: Machine) -> EtaInput:
    """Assignment-time features: forecast weather, full target quantity."""
    return EtaInput(
        task_type=task.task_type,
        material_type=task.material_type,
        target_quantity=task.target_quantity,
        skill_level=task.operator_skill_at_assignment,
        machine_type=machine.machine_type,
        machine_age=machine.machine_age,
        weather=shift.weather_forecast,
    )


def apply_assignment_estimate(
    task: Task, estimate: EtaEstimate, aggregates_version: str, *, reassignment: bool = False
) -> None:
    """Store the assignment-time estimate.

    Refuses to overwrite an existing one, except for a reassignment before
    start: that is a new assignment and gets a fresh estimate.
    """
    if task.raw_predicted_time is not None and not reassignment:
        raise ValidationError(
            "Task already has an assignment-time estimate. raw_predicted_time cannot change.",
            details={"task_id": task.task_id},
        )
    if reassignment and task.actual_start is not None:
        raise ValidationError(
            "A started task keeps its assignment-time estimate.",
            details={"task_id": task.task_id},
        )
    planning_eta = round(estimate.raw_minutes + buffer_minutes(), 1)
    task.raw_predicted_time = estimate.raw_minutes
    task.planning_eta = planning_eta
    task.scheduled_end = task.scheduled_start + timedelta(minutes=planning_eta)
    task.model_version = estimate.model_version
    task.aggregates_version = aggregates_version
    task.is_fallback = estimate.is_fallback


async def estimate_at_assignment(
    session: AsyncSession,
    task: Task,
    predictor: EtaPredictor | None = None,
    table: AggregateTable | None = None,
    *,
    reassignment: bool = False,
) -> EtaEstimate:
    """Predict and store the ETA for a newly assigned or reassigned task."""
    predictor = predictor or get_predictor()
    table = table or await load_aggregate_table(session, EtaAggregate)
    shift, machine = await _load_context(session, task)

    history = table.history(
        shift.operator_id, shift.machine_id, machine.machine_type,
        task.operator_skill_at_assignment, task.task_type,
    )
    estimate = predictor.predict(assignment_input(task, shift, machine), history, task.quantity_unit)
    apply_assignment_estimate(task, estimate, table.version, reassignment=reassignment)
    await session.flush()
    log.info(
        "Assignment ETA computed",
        extra={
            "task_id": task.task_id,
            "raw_predicted_time": estimate.raw_minutes,
            "is_fallback": estimate.is_fallback,
            "model_version": estimate.model_version,
        },
    )
    return estimate


# ---------------------------------------------------------------------------
# Admin breakdown
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EtaBreakdown:
    task_id: str
    historical_average_minutes: float
    raw_predicted_time: float | None
    buffer_minutes: float
    planning_eta: float | None
    revised_predicted_time: float | None
    revised_at: datetime | None
    operator_eta: float | None
    is_fallback: bool
    model_version: str | None
    aggregates_version: str | None
    current_model_version: str
    unusual_features: list[UnusualFeature]


async def eta_breakdown(
    session: AsyncSession,
    task_id: str,
    predictor: EtaPredictor | None = None,
) -> EtaBreakdown:
    """Full ETA breakdown for the admin view.

    Stored values come from the task. The historical average and the
    unusual features are recomputed from the assignment-time inputs with the
    current model and aggregates; current_model_version shows which model
    that was.
    """
    predictor = predictor or get_predictor()
    result = await session.execute(select(Task).where(Task.task_id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError(f"Task {task_id} not found.")

    shift, machine = await _load_context(session, task)
    table = await load_aggregate_table(session, EtaAggregate)
    history = table.history(
        shift.operator_id, shift.machine_id, machine.machine_type,
        task.operator_skill_at_assignment, task.task_type,
    )
    current = predictor.predict(assignment_input(task, shift, machine), history, task.quantity_unit)

    return EtaBreakdown(
        task_id=task.task_id,
        historical_average_minutes=current.historical_average_minutes,
        raw_predicted_time=task.raw_predicted_time,
        buffer_minutes=buffer_minutes(),
        planning_eta=task.planning_eta,
        revised_predicted_time=task.revised_predicted_time,
        revised_at=task.revised_at,
        operator_eta=displayed_eta(task.planning_eta, task.revised_predicted_time),
        is_fallback=task.is_fallback,
        model_version=task.model_version,
        aggregates_version=task.aggregates_version,
        current_model_version=current.model_version,
        unusual_features=current.unusual_features,
    )
