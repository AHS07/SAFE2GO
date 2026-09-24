"""Cloud analytics storage for records received from Kafka.

apply_records writes one decoded batch in the caller's transaction:
  - raw ticks into telemetry_archive, one row per event_id;
  - gap markers into telemetry_gap;
  - incidents and behavior events into stream_event;
  - the per-machine, per-minute rollup for every minute that gained a tick,
    recomputed from the archive.

Every insert ignores an event_id it already has, and rollups are recomputed
rather than incremented, so duplicate or reordered delivery gives the same
result as a single in-order delivery.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import wall_clock_now
from app.db.models.cloud.stream import (
    FleetMinuteRollup,
    StreamEvent,
    TelemetryArchive,
    TelemetryGap,
)
from app.shared.enums import StreamKind
from app.stream.codec import Envelope, MalformedRecord

log = logging.getLogger("safe2go.stream.analytics")

_MINUTE = timedelta(minutes=1)


@dataclass
class ApplyResult:
    stored: int = 0
    duplicates: int = 0
    malformed: int = 0


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def _tick_row(envelope: Envelope, received_at: datetime) -> dict:
    data = envelope.data
    try:
        return {
            "event_id": envelope.event_id,
            "edge_seq": envelope.edge_seq,
            "machine_id": envelope.machine_id,
            "shift_id": str(data["shift_id"]),
            "operator_id": str(data["operator_id"]),
            "task_id": data.get("task_id"),
            "ts": envelope.event_time,
            "engine_running": bool(data["engine_running"]),
            "engine_rpm": float(data["engine_rpm"]),
            "hydraulic_active": bool(data["hydraulic_active"]),
            "machine_speed": float(data["machine_speed"]),
            "fuel_used": float(data["fuel_used"]),
            "load_cycles": int(data["load_cycles"]),
            "payload_pct": float(data["payload_pct"]),
            "received_at": received_at,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedRecord(f"Tick {envelope.event_id}: {exc}") from exc


def _gap_row(envelope: Envelope, received_at: datetime) -> dict:
    data = envelope.data
    try:
        return {
            "event_id": envelope.event_id,
            "machine_id": envelope.machine_id,
            "from_ts": datetime.fromisoformat(data["from_ts"]),
            "to_ts": datetime.fromisoformat(data["to_ts"]),
            "dropped_count": int(data["dropped_count"]),
            "dropped_at": datetime.fromisoformat(data["dropped_at"]),
            "received_at": received_at,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedRecord(f"Gap {envelope.event_id}: {exc}") from exc


def _event_row(envelope: Envelope, received_at: datetime) -> dict:
    data = envelope.data
    if envelope.kind == StreamKind.INCIDENT.value:
        event_type, severity = data.get("incident_type"), data.get("peak_severity")
    else:
        event_type, severity = data.get("event_type"), None
    if not event_type:
        raise MalformedRecord(f"Event {envelope.event_id} has no type.")
    return {
        "event_id": envelope.event_id,
        "machine_id": envelope.machine_id,
        "kind": envelope.kind,
        "event_type": str(event_type),
        "severity": severity,
        "ts": envelope.event_time,
        "payload": data,
        "received_at": received_at,
    }


async def _insert_new(session: AsyncSession, model: type, rows: list[dict]) -> list[dict]:
    """Insert rows whose event_id is new; return the ones actually inserted."""
    if not rows:
        return []
    stored = set((
        await session.execute(
            insert(model).values(rows).on_conflict_do_nothing(index_elements=["event_id"]).returning(model.event_id)
        )
    ).scalars().all())
    return [r for r in rows if r["event_id"] in stored]


_ROLLUP_SQL = text("""
    INSERT INTO cloud.fleet_minute_rollup (
        machine_id, bucket_start, ticks, engine_on_ticks, working_ticks, idle_ticks,
        avg_rpm, fuel_used, load_cycles, updated_at
    )
    SELECT
        machine_id,
        date_trunc('minute', ts) AS bucket_start,
        count(*),
        count(*) FILTER (WHERE engine_running),
        count(*) FILTER (WHERE engine_running AND hydraulic_active),
        count(*) FILTER (WHERE engine_running AND NOT hydraulic_active AND machine_speed = 0),
        avg(engine_rpm),
        max(fuel_used) - min(fuel_used),
        sum(load_cycles),
        :now
    FROM cloud.telemetry_archive
    WHERE machine_id = :machine_id
      AND ts >= :first AND ts < :last
      AND date_trunc('minute', ts) = ANY(:buckets)
    GROUP BY machine_id, date_trunc('minute', ts)
    ON CONFLICT (machine_id, bucket_start) DO UPDATE SET
        ticks = EXCLUDED.ticks,
        engine_on_ticks = EXCLUDED.engine_on_ticks,
        working_ticks = EXCLUDED.working_ticks,
        idle_ticks = EXCLUDED.idle_ticks,
        avg_rpm = EXCLUDED.avg_rpm,
        fuel_used = EXCLUDED.fuel_used,
        load_cycles = EXCLUDED.load_cycles,
        updated_at = EXCLUDED.updated_at
""")


async def recompute_rollups(session: AsyncSession, buckets: dict[str, set[datetime]]) -> None:
    """Rebuild the given (machine, minute) rollups from the archive."""
    now = wall_clock_now()
    for machine_id, minutes in buckets.items():
        ordered = sorted(minutes)
        await session.execute(_ROLLUP_SQL, {
            "machine_id": machine_id,
            "first": ordered[0],
            "last": ordered[-1] + _MINUTE,
            "buckets": ordered,
            "now": now,
        })


async def apply_records(session: AsyncSession, envelopes: list[Envelope]) -> ApplyResult:
    """Store one batch. The caller commits, then commits the Kafka offsets."""
    result = ApplyResult()
    received_at = wall_clock_now()
    ticks: list[dict] = []
    gaps: list[dict] = []
    events: list[dict] = []
    for envelope in envelopes:
        try:
            if envelope.kind == StreamKind.TELEMETRY.value:
                ticks.append(_tick_row(envelope, received_at))
            elif envelope.kind == StreamKind.GAP.value:
                gaps.append(_gap_row(envelope, received_at))
            else:
                events.append(_event_row(envelope, received_at))
        except MalformedRecord as exc:
            result.malformed += 1
            log.warning("Stream record skipped", extra={"event_id": envelope.event_id, "reason": str(exc)[:200]})

    # One batch can hold the same event twice (a resend landing next to the original).
    ticks = list({r["event_id"]: r for r in ticks}.values())
    gaps = list({r["event_id"]: r for r in gaps}.values())
    events = list({r["event_id"]: r for r in events}.values())

    new_ticks = await _insert_new(session, TelemetryArchive, ticks)
    new_gaps = await _insert_new(session, TelemetryGap, gaps)
    new_events = await _insert_new(session, StreamEvent, events)
    result.stored = len(new_ticks) + len(new_gaps) + len(new_events)
    result.duplicates = (len(envelopes) - result.malformed) - result.stored

    buckets: dict[str, set[datetime]] = defaultdict(set)
    for row in new_ticks:
        buckets[row["machine_id"]].add(_minute(row["ts"]))
    if buckets:
        await recompute_rollups(session, buckets)
    return result


# ---------------------------------------------------------------------------
# Read models for the admin pipeline page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveSummary:
    ticks: int
    machines: int
    first_ts: datetime | None
    last_ts: datetime | None
    gaps: list[TelemetryGap]
    dropped_total: int
    events_by_type: list[tuple[str, str, int]]


async def archive_summary(session: AsyncSession, recent_gaps: int = 10) -> ArchiveSummary:
    ticks, machines, first_ts, last_ts = (
        await session.execute(select(
            func.count(), func.count(func.distinct(TelemetryArchive.machine_id)),
            func.min(TelemetryArchive.ts), func.max(TelemetryArchive.ts),
        ))
    ).one()
    gaps = list((
        await session.execute(select(TelemetryGap).order_by(TelemetryGap.dropped_at.desc()).limit(recent_gaps))
    ).scalars().all())
    dropped = (await session.execute(select(func.coalesce(func.sum(TelemetryGap.dropped_count), 0)))).scalar_one()
    events = (
        await session.execute(
            select(StreamEvent.kind, StreamEvent.event_type, func.count())
            .group_by(StreamEvent.kind, StreamEvent.event_type)
            .order_by(func.count().desc())
        )
    ).all()
    return ArchiveSummary(
        ticks=ticks,
        machines=machines,
        first_ts=first_ts,
        last_ts=last_ts,
        gaps=gaps,
        dropped_total=int(dropped),
        events_by_type=[(k, t, n) for k, t, n in events],
    )


async def recent_rollups(session: AsyncSession, minutes: int) -> list[FleetMinuteRollup]:
    """The last N minutes of rollups, counted back from the newest minute (sim time)."""
    latest = (await session.execute(select(func.max(FleetMinuteRollup.bucket_start)))).scalar_one()
    if latest is None:
        return []
    return list((
        await session.execute(
            select(FleetMinuteRollup)
            .where(FleetMinuteRollup.bucket_start > latest - timedelta(minutes=minutes))
            .order_by(FleetMinuteRollup.machine_id, FleetMinuteRollup.bucket_start)
        )
    ).scalars().all())
