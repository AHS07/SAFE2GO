"""Edge spool: durable queue of records waiting for Kafka.

Live ticks and safety events are written here in the same transaction as
the change they describe, so a rolled-back tick never reaches Kafka. The
write sits inside a savepoint: if the spool fails, the tick and its safety
decisions still commit. Kafka is never called from here.

The spool is bounded. Above the limit the oldest unsent raw ticks are
dropped per machine, and a gap record (machine, time range, count) takes
their place, sent ahead of everything else. Safety events are never
dropped, and incidents also travel through the sync outbox regardless.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.clock import wall_clock_now
from app.db.models.edge.stream import StreamSpool
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import StreamKind
from app.sync.outbox import encode_payload

log = logging.getLogger("safe2go.stream.spool")

PRIORITY: dict[str, int] = {
    StreamKind.GAP.value: 0,
    StreamKind.INCIDENT.value: 1,
    StreamKind.BEHAVIOR_EVENT.value: 1,
    StreamKind.TELEMETRY.value: 2,
}

# Process counters: records written, and writes that failed (tick kept,
# record not spooled). The forwarder derives the arrival rate from the first.
_written = 0
_write_failures = 0


def written_total() -> int:
    return _written


def write_failures() -> int:
    return _write_failures


def _json_safe(payload: dict) -> dict:
    return json.loads(encode_payload(payload))


async def _add(
    session: AsyncSession, kind: StreamKind, machine_id: str, event_time: datetime, payload: dict, event_id: str
) -> None:
    global _written, _write_failures
    try:
        async with session.begin_nested():
            session.add(StreamSpool(
                event_id=event_id,
                kind=kind.value,
                priority=PRIORITY[kind.value],
                machine_id=machine_id,
                event_time=event_time,
                spooled_at=wall_clock_now(),
                payload=_json_safe(payload),
            ))
        _written += 1
    except Exception as exc:  # noqa: BLE001 - the spool must never fail the tick
        _write_failures += 1
        log.error(
            "Spool write failed; record not forwarded",
            extra={"kind": kind.value, "machine_id": machine_id, "error": str(exc)[:200]},
        )


async def spool_tick(session: AsyncSession, tick: TelemetryTick, telemetry_id: str) -> None:
    """Queue a live tick for Kafka. The telemetry row id is the event id."""
    if not get_settings().kafka_enabled:
        return
    await _add(
        session, StreamKind.TELEMETRY, tick.machine_id, tick.timestamp, tick.model_dump(mode="json"), telemetry_id
    )


async def spool_event(
    session: AsyncSession, kind: StreamKind, machine_id: str, event_time: datetime, payload: dict
) -> None:
    """Queue an incident or behavior event change for Kafka (fleet analytics copy)."""
    if not get_settings().kafka_enabled:
        return
    await _add(session, kind, machine_id, event_time, payload, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    machine_id: str
    from_ts: datetime
    to_ts: datetime
    dropped_count: int


async def backlog_total(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(StreamSpool))).scalar_one()


async def raw_backlog(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(StreamSpool).where(StreamSpool.kind == StreamKind.TELEMETRY.value)
        )
    ).scalar_one()


async def enforce_limit(session: AsyncSession, max_records: int, low_water: float) -> list[Gap]:
    """Drop the oldest unsent raw ticks above the limit and record a gap per machine.

    Runs in the caller's transaction; the caller commits. Returns the gaps.
    """
    backlog = await raw_backlog(session)
    if backlog <= max_records:
        return []
    excess = backlog - int(max_records * low_water)
    rows = (
        await session.execute(
            select(StreamSpool.seq, StreamSpool.machine_id, StreamSpool.event_time)
            .where(StreamSpool.kind == StreamKind.TELEMETRY.value)
            .order_by(StreamSpool.seq)
            .limit(excess)
        )
    ).all()
    by_machine: dict[str, list] = {}
    for row in rows:
        by_machine.setdefault(row.machine_id, []).append(row)

    gaps: list[Gap] = []
    now = wall_clock_now()
    for machine_id, dropped in by_machine.items():
        gap = Gap(
            machine_id=machine_id,
            from_ts=min(r.event_time for r in dropped),
            to_ts=max(r.event_time for r in dropped),
            dropped_count=len(dropped),
        )
        gaps.append(gap)
        session.add(StreamSpool(
            event_id=str(uuid.uuid4()),
            kind=StreamKind.GAP.value,
            priority=PRIORITY[StreamKind.GAP.value],
            machine_id=machine_id,
            event_time=gap.to_ts,
            spooled_at=now,
            payload=_json_safe({
                "machine_id": machine_id,
                "from_ts": gap.from_ts,
                "to_ts": gap.to_ts,
                "dropped_count": gap.dropped_count,
                "dropped_at": now,
            }),
        ))
    await session.execute(delete(StreamSpool).where(StreamSpool.seq.in_([r.seq for r in rows])))
    await session.flush()
    for gap in gaps:
        log.warning(
            "Spool full, oldest raw ticks dropped and reported as a gap",
            extra={
                "machine_id": gap.machine_id,
                "dropped_count": gap.dropped_count,
                "from_ts": gap.from_ts.isoformat(),
                "to_ts": gap.to_ts.isoformat(),
            },
        )
    return gaps


# ---------------------------------------------------------------------------
# Backlog view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineBacklog:
    machine_id: str
    records: int
    oldest_event_time: datetime


@dataclass(frozen=True)
class Backlog:
    total: int
    by_kind: dict[str, int]
    by_machine: list[MachineBacklog]
    oldest_spooled_at: datetime | None
    spool_bytes: int


async def backlog(session: AsyncSession) -> Backlog:
    by_kind = dict(
        (await session.execute(select(StreamSpool.kind, func.count()).group_by(StreamSpool.kind))).all()
    )
    machines = (
        await session.execute(
            select(StreamSpool.machine_id, func.count(), func.min(StreamSpool.event_time))
            .group_by(StreamSpool.machine_id)
            .order_by(StreamSpool.machine_id)
        )
    ).all()
    oldest = (await session.execute(select(func.min(StreamSpool.spooled_at)))).scalar_one()
    size = (await session.execute(text("SELECT pg_total_relation_size('edge.stream_spool')"))).scalar_one()
    return Backlog(
        total=sum(by_kind.values()),
        by_kind=by_kind,
        by_machine=[MachineBacklog(m, n, t) for m, n, t in machines],
        oldest_spooled_at=oldest,
        spool_bytes=int(size),
    )
