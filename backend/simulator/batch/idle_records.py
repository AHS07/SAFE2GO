"""Per-shift idle ratio records rebuilt from stored history.

The generator builds these while it runs; this reads the same values back
from cloud.shift and cloud.shift_summary so high idle ratio events can be
recomputed (after a threshold change) without regenerating the data.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.analytics import ShiftSummary
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from simulator.batch.edge_replay import ShiftIdleRecord
from simulator.batch.shifts import EVALUATION_SPLIT_START, N_DAYS, START_DATE


async def load_idle_records(session: AsyncSession) -> list[ShiftIdleRecord]:
    """Records for every generated history shift, in start order."""
    history_end = START_DATE + timedelta(days=N_DAYS)
    last_task_end = (
        select(Task.shift_id, func.max(Task.actual_end).label("last_end"))
        .group_by(Task.shift_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Shift, Machine.machine_type, ShiftSummary.idle_ratio, last_task_end.c.last_end)
            .join(Machine, Machine.machine_id == Shift.machine_id)
            .join(ShiftSummary, ShiftSummary.shift_id == Shift.shift_id)
            .outerjoin(last_task_end, last_task_end.c.shift_id == Shift.shift_id)
            .where(Shift.scheduled_start < history_end)
            .order_by(Shift.scheduled_start)
        )
    ).all()
    return [
        ShiftIdleRecord(
            shift_id=shift.shift_id,
            operator_id=shift.operator_id,
            machine_type=machine_type,
            start=shift.scheduled_start,
            # Overtime can run past the scheduled end.
            end=max(shift.scheduled_end, last_end) if last_end else shift.scheduled_end,
            idle_ratio=idle_ratio,
            in_baseline_window=shift.scheduled_start < EVALUATION_SPLIT_START,
        )
        for shift, machine_type, idle_ratio, last_end in rows
    ]
