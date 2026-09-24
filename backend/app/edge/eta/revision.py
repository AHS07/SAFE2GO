"""Edge ETA revision.

While a task is in progress, the edge revises its estimate when:
  - weather: the observed weather category differs from the one the
    current estimate used (the forecast, until a revision exists), or
  - pace: after at least 25% completion, the actual rate differs from the
    rate implied by the current estimate by more than 20%.

At most one revision per 10 minutes of simulation time.

    revised_predicted_time = elapsed_working_time + model(remaining_quantity, current conditions)

The revision goes to revised_predicted_time and revised_at. It never
touches raw_predicted_time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import ETAThresholds, get_thresholds
from app.core.clock import sim_now
from app.db.models.edge.assignment import EdgeEtaAggregate, EdgeMachine, EdgeShift, EdgeTask
from app.shared.enums import TaskStatus
from app.shared.eta_aggregates import load_aggregate_table
from app.shared.eta_model import EtaInput, EtaPredictor, get_predictor

log = logging.getLogger("safe2go.edge_eta")

TRIGGER_WEATHER = "weather"
TRIGGER_PACE = "pace"


@dataclass(frozen=True)
class RevisionState:
    status: str
    target_quantity: float
    completed_quantity: float
    current_estimate_minutes: float
    elapsed_working_minutes: float
    weather_used: str
    weather_observed: str
    last_revised_at: datetime | None
    now: datetime


def elapsed_working_minutes(task: EdgeTask, now: datetime) -> float:
    """Working time so far: wall span minus closed pause and block periods."""
    if task.actual_start is None:
        return 0.0
    span = (now - task.actual_start).total_seconds() / 60.0
    return max(0.0, span - (task.paused_minutes or 0.0) - (task.blocked_minutes or 0.0))


def revision_trigger(state: RevisionState, cfg: ETAThresholds) -> str | None:
    """Return the trigger that fires for this state, or None."""
    if state.status != TaskStatus.IN_PROGRESS.value:
        return None
    if state.completed_quantity >= state.target_quantity or state.elapsed_working_minutes <= 0:
        return None
    if state.last_revised_at is not None:
        since = (state.now - state.last_revised_at).total_seconds()
        if since < cfg.revision_rate_limit_seconds:
            return None

    if state.weather_observed != state.weather_used:
        return TRIGGER_WEATHER

    completion_pct = state.completed_quantity / state.target_quantity * 100.0
    if completion_pct < cfg.revision_completion_threshold_pct or state.current_estimate_minutes <= 0:
        return None
    expected_rate = state.target_quantity / state.current_estimate_minutes
    actual_rate = state.completed_quantity / state.elapsed_working_minutes
    deviation_pct = abs(actual_rate / expected_rate - 1.0) * 100.0
    if deviation_pct > cfg.revision_pace_deviation_pct:
        return TRIGGER_PACE
    return None


async def revise_if_triggered(
    session: AsyncSession,
    task: EdgeTask,
    now: datetime | None = None,
    predictor: EtaPredictor | None = None,
) -> str | None:
    """Revise the task's ETA if a trigger fires. Returns the trigger or None."""
    if task.raw_predicted_time is None:
        return None
    now = now or sim_now()
    result = await session.execute(
        select(EdgeShift, EdgeMachine)
        .join(EdgeMachine, EdgeShift.machine_id == EdgeMachine.machine_id)
        .where(EdgeShift.shift_id == task.shift_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    shift, machine = row

    elapsed = elapsed_working_minutes(task, now)
    revised = task.revised_predicted_time
    # Actual weather is unknown on a newly created shift until observed.
    observed = shift.weather_actual or shift.weather_forecast
    state = RevisionState(
        status=task.status,
        target_quantity=task.target_quantity,
        completed_quantity=task.completed_quantity or 0.0,
        current_estimate_minutes=revised if revised is not None else task.raw_predicted_time,
        elapsed_working_minutes=elapsed,
        # A revision always uses observed conditions.
        weather_used=observed if revised is not None else shift.weather_forecast,
        weather_observed=observed,
        last_revised_at=task.revised_at,
        now=now,
    )
    trigger = revision_trigger(state, get_thresholds().eta)
    if trigger is None:
        return None

    predictor = predictor or get_predictor()
    table = await load_aggregate_table(session, EdgeEtaAggregate)
    history = table.history(
        shift.operator_id, shift.machine_id, machine.machine_type,
        task.operator_skill_at_assignment, task.task_type,
    )
    remaining = EtaInput(
        task_type=task.task_type,
        material_type=task.material_type,
        target_quantity=task.target_quantity - state.completed_quantity,
        skill_level=task.operator_skill_at_assignment,
        machine_type=machine.machine_type,
        machine_age=machine.machine_age,
        weather=observed,
    )
    estimate = predictor.predict(remaining, history, task.quantity_unit)
    task.revised_predicted_time = round(elapsed + estimate.raw_minutes, 1)
    task.revised_at = now
    task.revision_reason = trigger
    await session.flush()
    log.info(
        "ETA revised",
        extra={
            "task_id": task.task_id,
            "trigger": trigger,
            "revised_predicted_time": task.revised_predicted_time,
            "is_fallback": estimate.is_fallback,
        },
    )
    return trigger
