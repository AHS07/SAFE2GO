"""Current shift lookup for an operator or a machine.

The current shift is chosen by simulation time, so a shift the admin
creates for later does not replace the one being worked:
  1. the shift that contains the sim time now;
  2. otherwise the next upcoming shift;
  3. otherwise the most recent past shift.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.db.models.edge.assignment import EdgeShift


async def _current_shift(session: AsyncSession, column, value: str, now: datetime) -> EdgeShift | None:  # noqa: ANN001 - SQLAlchemy column
    base = select(EdgeShift).where(column == value)
    candidates = (
        base.where(EdgeShift.scheduled_start <= now)
        .where(EdgeShift.scheduled_end > now)
        .order_by(EdgeShift.scheduled_start.desc()),
        base.where(EdgeShift.scheduled_start > now).order_by(EdgeShift.scheduled_start.asc()),
        base.order_by(EdgeShift.scheduled_start.desc()),
    )
    for query in candidates:
        shift = (await session.execute(query.limit(1))).scalar_one_or_none()
        if shift is not None:
            return shift
    return None


async def current_shift_for_operator(
    session: AsyncSession, operator_id: str, now: datetime | None = None
) -> EdgeShift | None:
    return await _current_shift(session, EdgeShift.operator_id, operator_id, now or sim_now())


async def current_shift_for_machine(
    session: AsyncSession, machine_id: str, now: datetime | None = None
) -> EdgeShift | None:
    return await _current_shift(session, EdgeShift.machine_id, machine_id, now or sim_now())
