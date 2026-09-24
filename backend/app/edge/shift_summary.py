"""Per-shift telemetry summary, sent to the cloud when the shift ends.

Same metrics as the batch summaries: idle means engine running with no
hydraulic activity and the machine stopped. Built from the live ticks the
edge stored for the shift.

Each tick stands for the time since the previous tick, capped at
behavior.max_tick_gap_seconds (the first tick counts as one nominal tick),
the same rule the behavior engine uses, so both agree when ticks arrive at
uneven intervals.
"""
from __future__ import annotations

from sqlalchemy import and_, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.db.models.edge.assignment import EdgeTask
from app.db.models.edge.telemetry import Telemetry
from app.shared.enums import TaskStatus

_SECONDS_PER_HOUR = 3600.0


async def build_shift_summary(session: AsyncSession, shift_id: str) -> dict | None:
    """Summary for the shift, or None when the edge has no ticks for it."""
    nominal = float(get_settings().sim_tick_seconds)
    cap = get_thresholds().behavior.max_tick_gap_seconds

    gap = func.extract("epoch", Telemetry.timestamp - func.lag(Telemetry.timestamp).over(order_by=Telemetry.timestamp))
    ticks = (
        select(
            Telemetry.engine_running,
            Telemetry.hydraulic_active,
            Telemetry.machine_speed,
            Telemetry.load_cycles,
            Telemetry.fuel_used,
            func.least(func.coalesce(gap, literal(nominal)), literal(cap)).label("dt"),
        )
        .where(Telemetry.shift_id == shift_id)
        .subquery()
    )
    idle = and_(ticks.c.engine_running, ~ticks.c.hydraulic_active, ticks.c.machine_speed == 0)
    row = (
        await session.execute(
            select(
                func.count().label("ticks"),
                func.coalesce(func.sum(ticks.c.dt).filter(ticks.c.engine_running), 0.0).label("engine_seconds"),
                func.coalesce(func.sum(ticks.c.dt).filter(idle), 0.0).label("idle_seconds"),
                func.coalesce(func.sum(ticks.c.load_cycles), 0).label("cycles"),
                (func.max(ticks.c.fuel_used) - func.min(ticks.c.fuel_used)).label("fuel"),
            )
        )
    ).one()
    if row.ticks == 0:
        return None

    completed = (
        await session.execute(
            select(func.coalesce(func.sum(EdgeTask.completed_quantity), 0.0))
            .where(EdgeTask.shift_id == shift_id)
            .where(EdgeTask.status != TaskStatus.CANCELLED.value)
        )
    ).scalar_one()

    engine_seconds = float(row.engine_seconds)
    idle_seconds = float(row.idle_seconds)
    engine_hours = engine_seconds / _SECONDS_PER_HOUR
    return {
        "shift_id": shift_id,
        "engine_hours": round(engine_hours, 3),
        "idle_minutes": round(idle_seconds / 60.0, 2),
        "idle_ratio": round(idle_seconds / engine_seconds, 4) if engine_seconds > 0 else 0.0,
        "cycle_count": int(row.cycles),
        "completed_quantity": round(float(completed), 3),
        "fuel_used": round(float(row.fuel or 0.0), 3),
        "cycle_rate": round(row.cycles / engine_hours, 3) if engine_hours > 0 else 0.0,
    }
