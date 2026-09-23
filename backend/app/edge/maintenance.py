"""Service suggestion for the operator's machine.

Uses the live meter from the latest tick while the machine is streaming,
otherwise the synced machine meter.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import get_thresholds
from app.core.errors import NotFoundError
from app.db.models.edge.assignment import EdgeMachine
from app.edge.telemetry.state import latest_tick
from app.shared.maintenance import ServiceSuggestion, service_suggestion


async def machine_service(session: AsyncSession, machine_id: str) -> ServiceSuggestion:
    machine = await session.get(EdgeMachine, machine_id)
    if machine is None:
        raise NotFoundError(f"Machine {machine_id} not found.")
    tick = latest_tick(machine_id)
    meter = max(machine.engine_hours, tick.engine_hours) if tick is not None else machine.engine_hours
    return service_suggestion(
        meter, machine.last_service_engine_hours, machine.service_interval_hours, get_thresholds().maintenance
    )
