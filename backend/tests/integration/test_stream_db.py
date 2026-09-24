"""Telemetry streaming (edge spool -> Kafka -> cloud analytics) against the database.

Kafka is replaced by tests/stream_fakes.py, so broker outages, lost
acknowledgements, and crashes can be injected exactly. The real-broker
path is covered in test_stream_kafka.py.
"""
from __future__ import annotations

import random
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.cloud.stream.analytics import apply_records
from app.cloud.stream.consumer import AnalyticsConsumer
from app.config.settings import get_settings
from app.core.connectivity import set_cloud_reachable
from app.core.security import create_access_token
from app.db.models.cloud.stream import (
    FleetMinuteRollup,
    StreamEvent,
    TelemetryArchive,
    TelemetryGap,
)
from app.db.models.edge.incident import Incident
from app.db.models.edge.stream import StreamSpool
from app.db.models.edge.telemetry import Telemetry
from app.db.session import get_db
from app.edge.stream import spool
from app.edge.stream.forwarder import ForwarderState, StreamForwarder
from app.edge.telemetry.pipeline import EdgePipeline
from app.main import app
from app.shared.enums import StreamKind
from app.stream.codec import Envelope
from tests.integration.conftest import T0, _Recorder, db_session, make_tick
from tests.stream_fakes import FakeBroker, FakeConsumer, FakeProducer, ManualClock, topics_ready

TELEMETRY_TOPIC = "test.telemetry"
EVENTS_TOPIC = "test.events"
GROUP = "test-analytics"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def stream(conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    """Streaming on, empty spool and analytics tables (inside the rolled-back test transaction)."""
    settings = get_settings()
    for name, value in {
        "kafka_enabled": True,
        "kafka_topic_telemetry": TELEMETRY_TOPIC,
        "kafka_topic_events": EVENTS_TOPIC,
        "kafka_consumer_group": GROUP,
        "stream_batch_size": 500,
        "stream_max_rate": 0.0,        # unpaced unless a test sets it
        "stream_backoff_max_seconds": 30.0,
        "stream_send_timeout_seconds": 5.0,
        "stream_spool_max_records": 250_000,
        "stream_spool_low_water": 0.9,
    }.items():
        monkeypatch.setattr(settings, name, value)
    async with db_session(conn) as session:
        for model in (StreamSpool, TelemetryArchive, TelemetryGap, StreamEvent, FleetMinuteRollup):
            await session.execute(delete(model))
        await session.commit()
    set_cloud_reachable(True)
    broker = FakeBroker()
    clock = ManualClock()
    try:
        yield {"broker": broker, "clock": clock}
    finally:
        set_cloud_reachable(True)


def _forwarder(conn: AsyncConnection, stream: dict, factory=None) -> StreamForwarder:  # noqa: ANN001 - session factory
    broker, clock = stream["broker"], stream["clock"]
    return StreamForwarder(
        factory or (lambda: db_session(conn)),
        producer_factory=lambda _s: FakeProducer(broker),
        topics_ready=topics_ready,
        sleep=clock.sleep,
        clock=clock,
    )


def _consumer(conn: AsyncConnection, broker: FakeBroker, factory=None) -> AnalyticsConsumer:  # noqa: ANN001
    return AnalyticsConsumer(
        factory or (lambda: db_session(conn)),
        consumer_factory=lambda _s: FakeConsumer(broker, GROUP, (TELEMETRY_TOPIC, EVENTS_TOPIC)),
        topics_ready=topics_ready,
    )


async def _spool_ticks(conn: AsyncConnection, world: dict, seconds: range, machine_id: str | None = None) -> None:
    """Spool ticks directly (what ingest does for each live tick)."""
    async with db_session(conn) as session:
        for second in seconds:
            tick = make_tick(world, second, **({"machine_id": machine_id} if machine_id else {}))
            await spool.spool_tick(session, tick, str(uuid.uuid4()))
        await session.commit()


async def _spool_rows(conn: AsyncConnection) -> list[StreamSpool]:
    async with db_session(conn) as session:
        return list((await session.execute(select(StreamSpool).order_by(StreamSpool.seq))).scalars().all())


async def _count(conn: AsyncConnection, model: type) -> int:
    async with db_session(conn) as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _drain(forwarder: StreamForwarder, passes: int = 50) -> None:
    for _ in range(passes):
        if await forwarder.run_once() == 0 and forwarder.stats.state == ForwarderState.IDLE:
            return
    raise AssertionError("spool did not drain")


async def _consume_all(consumer: AnalyticsConsumer) -> None:
    assert await consumer._ensure_started()
    while await consumer.poll_once():
        pass


class _FailOnce:
    """Session factory whose Nth commit (1-based) raises, like a crash or database fault."""

    def __init__(self, conn: AsyncConnection, fail_commit_number: int) -> None:
        self.conn = conn
        self.commits = 0
        self.fail_at = fail_commit_number

    def __call__(self) -> AsyncSession:
        session = db_session(self.conn)
        real_commit = session.commit

        async def commit() -> None:
            self.commits += 1
            if self.commits == self.fail_at:
                raise RuntimeError("crashed before the commit")
            await real_commit()
        session.commit = commit
        return session


# ---------------------------------------------------------------------------
# Spool writes
# ---------------------------------------------------------------------------


async def test_nothing_is_spooled_when_streaming_is_off(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "kafka_enabled", False)
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    for second in range(3):
        await pipeline.process(make_tick(world, second))
    assert await _spool_rows(conn) == []
    async with db_session(conn) as session:
        stored = (await session.execute(
            select(func.count()).select_from(Telemetry).where(Telemetry.shift_id == world["shift_id"])
        )).scalar_one()
    assert stored == 3


async def test_each_live_tick_is_spooled_in_order_with_its_telemetry_id(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    for second in range(5):
        await pipeline.process(make_tick(world, second))
    rows = await _spool_rows(conn)
    assert [r.kind for r in rows] == ["telemetry"] * 5
    assert [r.event_time for r in rows] == [T0 + timedelta(seconds=s) for s in range(5)]
    async with db_session(conn) as session:
        ids = set((await session.execute(
            select(Telemetry.telemetry_id).where(Telemetry.shift_id == world["shift_id"])
        )).scalars().all())
    assert {r.event_id for r in rows} == ids


async def test_a_rolled_back_tick_is_never_spooled(conn: AsyncConnection, world: dict, stream: dict) -> None:
    factory = _FailOnce(conn, fail_commit_number=2)
    pipeline = EdgePipeline(factory, _Recorder())
    for second in range(3):
        await pipeline.process(make_tick(world, second))
    assert [r.event_time for r in await _spool_rows(conn)] == [T0, T0 + timedelta(seconds=2)]


async def test_a_spool_failure_never_fails_the_tick_or_safety(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An out-of-range priority makes the database reject every spool insert.
    monkeypatch.setitem(spool.PRIORITY, StreamKind.TELEMETRY.value, 99_999)
    monkeypatch.setitem(spool.PRIORITY, StreamKind.INCIDENT.value, 99_999)
    failures = spool.write_failures()
    sent = _Recorder()
    pipeline = EdgePipeline(lambda: db_session(conn), sent)
    for second in range(4):
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))

    assert spool.write_failures() > failures
    assert await _spool_rows(conn) == []
    async with db_session(conn) as session:
        incidents = (await session.execute(
            select(Incident).where(Incident.shift_id == world["shift_id"], Incident.incident_type == "proximity")
        )).scalars().all()
    assert len(incidents) == 1, "the safety decision committed although the spool failed"
    assert [m["event"] for m in sent.of_type("incident")][:1] == ["opened"]


async def test_incidents_are_spooled_as_safety_events_ahead_of_ticks(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    for second in range(4):
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))
    rows = await _spool_rows(conn)
    events = [r for r in rows if r.kind == StreamKind.INCIDENT.value]
    assert events and all(r.priority < spool.PRIORITY[StreamKind.TELEMETRY.value] for r in events)
    assert events[0].payload["incident_type"] == "proximity"


# ---------------------------------------------------------------------------
# Forwarder
# ---------------------------------------------------------------------------


async def test_link_down_keeps_records_then_drains_in_order_per_machine(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    other_machine = str(uuid.uuid4())
    await _spool_ticks(conn, world, range(0, 30))
    await _spool_ticks(conn, world, range(0, 20), machine_id=other_machine)
    forwarder = _forwarder(conn, stream)

    set_cloud_reachable(False)
    assert await forwarder.run_once() == 0
    assert forwarder.stats.state == ForwarderState.LINK_DOWN
    assert stream["broker"].records() == []
    assert len(await _spool_rows(conn)) == 50

    set_cloud_reachable(True)
    await _drain(forwarder)
    assert await _spool_rows(conn) == []
    envelopes = stream["broker"].envelopes(TELEMETRY_TOPIC)
    for machine in (world["machine_id"], other_machine):
        mine = [e for e in envelopes if e.machine_id == machine]
        assert [e.edge_seq for e in mine] == sorted(e.edge_seq for e in mine)
        assert [e.event_time for e in mine] == sorted(e.event_time for e in mine)
    records = stream["broker"].records(TELEMETRY_TOPIC)
    partitions = {r.key: {x.partition for x in records if x.key == r.key} for r in records}
    assert all(len(p) == 1 for p in partitions.values()), "one partition per machine keeps its order"
    assert forwarder.stats.sent_total == 50


async def test_broker_outage_backs_off_without_losing_records(conn: AsyncConnection, world: dict, stream: dict) -> None:
    await _spool_ticks(conn, world, range(10))
    broker, clock = stream["broker"], stream["clock"]
    forwarder = _forwarder(conn, stream)
    broker.up = False

    assert await forwarder.run_once() == 0
    assert forwarder.stats.state == ForwarderState.BROKER_DOWN
    assert "unreachable" in (forwarder.stats.last_error or "")
    first_retry = forwarder.stats.retry_in_seconds
    assert await forwarder.run_once() == 0, "inside the backoff window nothing is attempted"
    clock.now += first_retry
    assert await forwarder.run_once() == 0
    assert forwarder.stats.retry_in_seconds > first_retry, "backoff grows"
    assert len(await _spool_rows(conn)) == 10

    broker.up = True
    clock.now += 60
    await _drain(forwarder)
    assert len(broker.records()) == 10
    assert await _spool_rows(conn) == []


async def test_unacknowledged_records_stay_and_are_resent_before_later_ones(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    await _spool_ticks(conn, world, range(10))
    rows = await _spool_rows(conn)
    stream["broker"].fail_event_ids.add(rows[4].event_id)
    forwarder = _forwarder(conn, stream)

    assert await forwarder.run_once() == 4, "records before the failure are done"
    assert [r.seq for r in await _spool_rows(conn)] == [r.seq for r in rows[4:]]
    assert forwarder.stats.state == ForwarderState.BROKER_DOWN

    stream["clock"].now += 60
    await _drain(forwarder)
    assert await _spool_rows(conn) == []

    # Records 6-10 reached the broker twice; the consumer stores each once.
    consumer = _consumer(conn, stream["broker"])
    await _consume_all(consumer)
    assert await _count(conn, TelemetryArchive) == 10
    assert consumer.stats.duplicates_total == 5


async def test_crash_after_ack_before_delete_resends_and_stays_correct(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    await _spool_ticks(conn, world, range(8))
    # Commit 1 is the spool-limit check; commit 2 is the delete after the broker acknowledged.
    forwarder = _forwarder(conn, stream, factory=_FailOnce(conn, fail_commit_number=2))
    with pytest.raises(RuntimeError):
        await forwarder.run_once()
    assert len(stream["broker"].records()) == 8
    assert len(await _spool_rows(conn)) == 8, "not deleted: the records will be sent again"

    await _drain(forwarder)
    assert len(stream["broker"].records()) == 16
    consumer = _consumer(conn, stream["broker"])
    await _consume_all(consumer)
    assert await _count(conn, TelemetryArchive) == 8
    async with db_session(conn) as session:
        ticks = (await session.execute(select(func.sum(FleetMinuteRollup.ticks)))).scalar_one()
    assert ticks == 8


async def test_replay_is_paced_to_the_configured_rate(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "stream_batch_size", 100)
    monkeypatch.setattr(get_settings(), "stream_max_rate", 200.0)
    await _spool_ticks(conn, world, range(300))
    forwarder = _forwarder(conn, stream)
    clock = stream["clock"]
    started = clock.now
    await _drain(forwarder)
    elapsed = clock.now - started
    assert elapsed >= 300 / 200.0 - 1e-6, "300 records at 200/s take at least 1.5 s"
    assert all(s == pytest.approx(0.5) for s in clock.sleeps), "each batch of 100 is spread over 0.5 s"


async def test_full_spool_drops_oldest_ticks_and_reports_a_gap_first(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "stream_spool_max_records", 50)
    monkeypatch.setattr(get_settings(), "stream_spool_low_water", 0.8)
    other_machine = str(uuid.uuid4())
    await _spool_ticks(conn, world, range(0, 40))
    await _spool_ticks(conn, world, range(0, 20), machine_id=other_machine)
    async with db_session(conn) as session:
        await spool.spool_event(session, StreamKind.INCIDENT, world["machine_id"], T0, {
            "incident_id": "i-1", "incident_type": "proximity", "peak_severity": "critical",
        })
        await session.commit()

    forwarder = _forwarder(conn, stream)
    set_cloud_reachable(False)
    await forwarder.run_once()
    rows = await _spool_rows(conn)
    assert sum(1 for r in rows if r.kind == "telemetry") == 40, "dropped down to 80% of 50"
    assert sum(1 for r in rows if r.kind == "incident") == 1, "safety events are never dropped"
    gaps = {r.machine_id: r.payload for r in rows if r.kind == "gap"}
    # The 20 oldest ticks by spool order are the first machine's seconds 0-19.
    assert gaps == {world["machine_id"]: {
        "machine_id": world["machine_id"],
        "from_ts": T0.isoformat(),
        "to_ts": (T0 + timedelta(seconds=19)).isoformat(),
        "dropped_count": 20,
        "dropped_at": gaps[world["machine_id"]]["dropped_at"],
    }}
    assert forwarder.stats.dropped_total == 20

    set_cloud_reachable(True)
    await _drain(forwarder)
    assert stream["broker"].arrival_kinds()[:2] == ["gap", "incident"], "gap first, then safety, then ticks"
    assert len(stream["broker"].arrivals) == 42
    consumer = _consumer(conn, stream["broker"])
    await _consume_all(consumer)
    async with db_session(conn) as session:
        stored_gap = (await session.execute(select(TelemetryGap))).scalar_one()
    assert (stored_gap.machine_id, stored_gap.dropped_count) == (world["machine_id"], 20)
    assert await _count(conn, TelemetryArchive) == 40


async def test_gap_markers_and_safety_events_are_sent_before_raw_backlog(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "stream_batch_size", 5)
    await _spool_ticks(conn, world, range(20))
    async with db_session(conn) as session:
        await spool.spool_event(session, StreamKind.INCIDENT, world["machine_id"], T0, {
            "incident_id": "i-1", "incident_type": "tilt", "peak_severity": "warning",
        })
        await session.commit()
    forwarder = _forwarder(conn, stream)
    await forwarder.run_once()
    assert stream["broker"].arrival_kinds() == ["incident"] + ["telemetry"] * 4, "the safety event jumps the backlog"


async def test_backlog_drains_while_live_ticks_keep_arriving(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconnect under load: 2,000 queued ticks, 60 new ones per pass, batches of 500."""
    monkeypatch.setattr(get_settings(), "stream_batch_size", 500)
    await _spool_ticks(conn, world, range(2000))
    forwarder = _forwarder(conn, stream)
    backlog_after_each_pass = []
    next_second = 2000
    for _ in range(10):
        await _spool_ticks(conn, world, range(next_second, next_second + 60))
        next_second += 60
        await forwarder.run_once()
        backlog_after_each_pass.append(len(await _spool_rows(conn)))
        if backlog_after_each_pass[-1] == 0:
            break
    assert backlog_after_each_pass[-1] == 0
    assert all(b > a for b, a in zip(backlog_after_each_pass, backlog_after_each_pass[1:], strict=False)), (
        f"backlog shrinks every pass: {backlog_after_each_pass}"
    )
    assert len(stream["broker"].records()) == next_second


# ---------------------------------------------------------------------------
# Consumer and analytics
# ---------------------------------------------------------------------------


def _envelopes(machine_id: str, seconds: int, *, start_seq: int = 1) -> list[Envelope]:
    out = []
    for i in range(seconds):
        ts = T0 + timedelta(seconds=i)
        idle = i % 3 == 0
        out.append(Envelope(
            event_id=str(uuid.uuid4()),
            kind="telemetry",
            edge_seq=start_seq + i,
            machine_id=machine_id,
            event_time=ts,
            spooled_at=ts,
            data={
                "shift_id": "s-1", "operator_id": "o-1", "task_id": None,
                "engine_running": True, "engine_rpm": 1000.0 + i,
                "hydraulic_active": not idle, "machine_speed": 0.0,
                "fuel_used": 10.0 + i * 0.01, "load_cycles": 1 if i % 30 == 0 else 0, "payload_pct": 40.0,
            },
        ))
    return out


async def _rollups(conn: AsyncConnection) -> list[tuple]:
    async with db_session(conn) as session:
        rows = (await session.execute(
            select(FleetMinuteRollup).order_by(FleetMinuteRollup.machine_id, FleetMinuteRollup.bucket_start)
        )).scalars().all()
    return [
        (r.machine_id, r.bucket_start, r.ticks, r.engine_on_ticks, r.working_ticks, r.idle_ticks,
         round(r.avg_rpm, 6), round(r.fuel_used, 6), r.load_cycles)
        for r in rows
    ]


async def test_rollups_are_the_same_for_duplicate_and_out_of_order_delivery(conn: AsyncConnection, stream: dict) -> None:
    envelopes = _envelopes("m-a", 150) + _envelopes("m-b", 90)
    async with db_session(conn) as session:
        result = await apply_records(session, envelopes)
        await session.commit()
    assert (result.stored, result.duplicates) == (240, 0)
    in_order = await _rollups(conn)
    assert [r[2] for r in in_order if r[0] == "m-a"] == [60, 60, 30]
    assert in_order[0][5] == 20, "every third tick idles (engine on, no hydraulics, not moving)"

    async with db_session(conn) as session:
        for model in (TelemetryArchive, FleetMinuteRollup):
            await session.execute(delete(model))
        await session.commit()
    shuffled = envelopes + random.Random(7).sample(envelopes, 80)
    random.Random(3).shuffle(shuffled)
    for start in range(0, len(shuffled), 50):
        async with db_session(conn) as session:
            await apply_records(session, shuffled[start:start + 50])
            await session.commit()
    assert await _rollups(conn) == in_order
    assert await _count(conn, TelemetryArchive) == 240


async def test_consumer_commits_offsets_only_after_the_database(conn: AsyncConnection, world: dict, stream: dict) -> None:
    await _spool_ticks(conn, world, range(12))
    await _drain(_forwarder(conn, stream))
    broker = stream["broker"]

    crashing = _consumer(conn, broker, factory=_FailOnce(conn, fail_commit_number=1))
    assert await crashing._ensure_started()
    with pytest.raises(RuntimeError):
        await crashing.poll_once()
    assert broker.committed == {} or all(v == 0 for v in broker.committed.values())
    assert await _count(conn, TelemetryArchive) == 0

    restarted = _consumer(conn, broker)
    await _consume_all(restarted)
    assert await _count(conn, TelemetryArchive) == 12
    again = _consumer(conn, broker)
    await _consume_all(again)
    assert again.stats.received_total == 0, "a restart resumes from the committed offsets"


async def test_undecodable_records_are_skipped_and_counted(conn: AsyncConnection, world: dict, stream: dict) -> None:
    broker = stream["broker"]
    broker.append(TELEMETRY_TOPIC, b"m-x", b"not json")
    broker.append(TELEMETRY_TOPIC, b"m-x", b'{"v": 99}')
    await _spool_ticks(conn, world, range(3))
    await _drain(_forwarder(conn, stream))
    consumer = _consumer(conn, broker)
    await _consume_all(consumer)
    assert consumer.stats.malformed_total == 2
    assert await _count(conn, TelemetryArchive) == 3
    assert consumer.stats.lag == 0


async def test_consumer_reports_lag_for_a_slow_reader(conn: AsyncConnection, world: dict, stream: dict) -> None:
    await _spool_ticks(conn, world, range(30))
    await _drain(_forwarder(conn, stream))
    consumer = _consumer(conn, stream["broker"])
    assert await consumer._ensure_started()
    assert await consumer._lag() == 30
    await consumer.poll_once()
    assert consumer.stats.lag == 0


async def test_safety_events_are_stored_for_fleet_analytics(conn: AsyncConnection, world: dict, stream: dict) -> None:
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    for second in range(4):
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))
    await _drain(_forwarder(conn, stream))
    await _consume_all(_consumer(conn, stream["broker"]))
    async with db_session(conn) as session:
        events = (await session.execute(select(StreamEvent))).scalars().all()
    assert {(e.kind, e.event_type) for e in events} >= {("incident", "proximity")}


# ---------------------------------------------------------------------------
# Safety never depends on Kafka
# ---------------------------------------------------------------------------


async def test_safety_keeps_working_while_the_broker_is_down(conn: AsyncConnection, world: dict, stream: dict) -> None:
    stream["broker"].up = False
    forwarder = _forwarder(conn, stream)
    sent = _Recorder()
    pipeline = EdgePipeline(lambda: db_session(conn), sent)

    # Tests share one connection, so passes interleave with ticks instead of
    # running concurrently (the app gives each its own pooled connection).
    for second in range(4):
        await forwarder.run_once()
        stream["clock"].now += 60                 # every pass really tries the broker
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))
    await forwarder.run_once()

    assert [m["event"] for m in sent.of_type("incident")][:1] == ["opened"]
    assert len(sent.of_type("telemetry")) == 4
    assert forwarder.stats.state == ForwarderState.BROKER_DOWN
    assert sum(1 for r in await _spool_rows(conn) if r.kind == "telemetry") == 4


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@pytest.fixture
async def admin_http(conn: AsyncConnection) -> AsyncIterator[AsyncClient]:
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with db_session(conn) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    token = create_access_token(user_id=str(uuid.uuid4()), role="admin")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


async def test_pipeline_status_and_rollups_endpoints(
    admin_http: AsyncClient, conn: AsyncConnection, world: dict, stream: dict
) -> None:
    await _spool_ticks(conn, world, range(5))
    async with db_session(conn) as session:
        await apply_records(session, _envelopes("m-a", 90))
        await session.commit()

    status = (await admin_http.get("/api/admin/pipeline")).json()
    assert status["enabled"] is True
    assert status["spool"]["total"] == 5
    assert status["spool"]["by_machine"][0]["records"] == 5
    assert status["spool"]["oldest_age_seconds"] >= 0
    assert status["archive"]["ticks"] == 90
    assert status["topics"] == [TELEMETRY_TOPIC, EVENTS_TOPIC]

    rollups = (await admin_http.get("/api/admin/pipeline/rollups?minutes=60")).json()
    assert [m["machine_id"] for m in rollups] == ["m-a"]
    assert [p["ticks"] for p in rollups[0]["points"]] == [60, 30]


async def test_pipeline_endpoints_require_admin(admin_http: AsyncClient) -> None:
    token = create_access_token(user_id=str(uuid.uuid4()), role="operator", operator_id=str(uuid.uuid4()))
    response = await admin_http.get("/api/admin/pipeline", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
