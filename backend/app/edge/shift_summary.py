"""Per-shift telemetry summary, sent to the cloud when the shift ends.

Same metrics as the batch summaries: idle means engine running with no
hydraulic activity and the machine stopped. Built from the live ticks the
edge stored for the shift.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.models.edge.assignment import EdgeTask
from app.db.models.edge.telemetry import Telemetry
from app.shared.enums import TaskStatus

_SECONDS_PER_HOUR = 3600.0


async def build_shift_summary(session: AsyncSession, shift_id: str) -> dict | None:
    """Summary for the shift, or None when the edge has no ticks for it."""
    tick_seconds = float(get_settings().sim_tick_seconds)
    idle = and_(Telemetry.engine_running, ~Telemetry.hydraulic_active, Telemetry.machine_speed == 0)
    row = (
        await session.execute(
            select(
                func.count().label("ticks"),
                func.count().filter(Telemetry.engine_running).label("engine_ticks"),
                func.count().filter(idle).label("idle_ticks"),
                func.coalesce(func.sum(Telemetry.load_cycles), 0).label("cycles"),
                (func.max(Telemetry.fuel_used) - func.min(Telemetry.fuel_used)).label("fuel"),
            ).where(Telemetry.shift_id == shift_id)
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

    engine_seconds = row.engine_ticks * tick_seconds
    idle_seconds = row.idle_ticks * tick_seconds
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
