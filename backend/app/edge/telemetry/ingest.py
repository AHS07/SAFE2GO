"""Telemetry ingest service.

Validates incoming ticks, persists them to edge.telemetry, updates task
progress, and pushes the tick to the WebSocket broadcast queue.

A malformed tick is logged with a counter and discarded — it never
halts the stream.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import set_sim_time
from app.db.models.edge.telemetry import Telemetry
from app.schemas.telemetry import TelemetryTick

log = logging.getLogger("safe2go.ingest")


@dataclass
class IngestStats:
    accepted: int = 0
    rejected: int = 0
    rejection_reasons: list[str] = field(default_factory=list)


# Module-level stats — reset on each simulator start.
_stats = IngestStats()


def get_stats() -> IngestStats:
    return _stats


def reset_stats() -> None:
    global _stats
    _stats = IngestStats()


async def ingest_tick(
    tick: TelemetryTick,
    session: AsyncSession,
    ws_queue: asyncio.Queue | None = None,
    safety_engine: Any | None = None,
    behavior_engine: Any | None = None,
    task_status: str | None = None,
) -> None:
    """Persist one validated tick, run safety + behavior engines, push to WS queue."""
    # Advance the simulation clock to this tick's timestamp.
    set_sim_time(tick.timestamp)

    row = Telemetry(
        telemetry_id=str(uuid.uuid4()),
        timestamp=tick.timestamp,
        machine_id=tick.machine_id,
        operator_id=tick.operator_id,
        task_id=tick.task_id,
        shift_id=tick.shift_id,
        engine_running=tick.engine_running,
        engine_rpm=tick.engine_rpm,
        engine_hours=tick.engine_hours,
        fuel_used=tick.fuel_used,
        hydraulic_active=tick.hydraulic_active,
        machine_speed=tick.machine_speed,
        load_cycles=tick.load_cycles,
        payload_pct=tick.payload_pct,
        cycle_payload_pct=tick.cycle_payload_pct,
        seatbelt_status=tick.seatbelt_status,
        seat_occupied=tick.seat_occupied,
        park_brake=tick.park_brake,
        gear_state=tick.gear_state,
        proximity_distance=tick.proximity_distance,
        ambient_temp=tick.ambient_temp,
        visibility=tick.visibility,
        tilt_angle=tick.tilt_angle,
    )
    session.add(row)
    await session.flush()

    _stats.accepted += 1

    # Run safety engine.
    if safety_engine is not None:
        await safety_engine.process_tick(tick, session)

    # Run behavior engine.
    if behavior_engine is not None:
        await behavior_engine.process_tick(tick, task_status, session)

    if ws_queue is not None:
        # Drop if the consumer is too slow; never block the ingest path.
        with contextlib.suppress(asyncio.QueueFull):
            ws_queue.put_nowait({"type": "telemetry", "data": tick.model_dump(mode="json")})


def validate_raw(raw: dict) -> TelemetryTick | None:
    """Parse and validate a raw dict. Returns None and logs on failure."""
    try:
        return TelemetryTick.model_validate(raw)
    except ValidationError as exc:
        _stats.rejected += 1
        reason = str(exc)
        _stats.rejection_reasons.append(reason[:200])
        log.warning(
            "Telemetry tick rejected",
            extra={"reason": reason[:200], "total_rejected": _stats.rejected},
        )
        return None
