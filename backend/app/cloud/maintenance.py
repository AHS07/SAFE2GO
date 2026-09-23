"""Cloud hour meters.

The meter is the last service reading plus the engine hours of every
summarized shift. The generator sets it once for history; afterwards each
shift summary synced from the edge adds its hours, and the updated machine
list is sent back to the edge.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.machine import Machine
from app.db.models.cloud.shift import Shift

# Same statement as migration 0007.
_RECOMPUTE_SQL = text("""
    UPDATE cloud.machine AS m
    SET engine_hours = m.last_service_engine_hours + COALESCE((
        SELECT SUM(ss.engine_hours)
        FROM cloud.shift_summary ss
        JOIN cloud.shift s ON s.shift_id = ss.shift_id
        WHERE s.machine_id = m.machine_id
    ), 0)
""")


async def recompute_hour_meters(session: AsyncSession) -> None:
    await session.execute(_RECOMPUTE_SQL)


async def add_shift_hours(session: AsyncSession, shift_id: str, delta_hours: float) -> Machine | None:
    """Add a shift's newly reported engine hours to its machine's meter."""
    shift = await session.get(Shift, shift_id)
    if shift is None or delta_hours == 0:
        return None
    machine = await session.get(Machine, shift.machine_id)
    if machine is not None:
        machine.engine_hours = (machine.engine_hours or 0.0) + delta_hours
    return machine
