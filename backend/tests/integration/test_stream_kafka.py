"""Streaming against a real Kafka broker and the real aiokafka clients.

The end-to-end tests run only when a broker answers on localhost:9092
(docker compose up -d kafka); each uses its own topics and consumer group.
The unreachable-broker test always runs: it points the real client at a
closed port.
"""
from __future__ import annotations

import socket
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.cloud.stream.consumer import AnalyticsConsumer
from app.config.settings import get_settings
from app.core.connectivity import set_cloud_reachable
from app.db.models.cloud.stream import (
    FleetMinuteRollup,
    StreamEvent,
    TelemetryArchive,
    TelemetryGap,
)
from app.db.models.edge.stream import StreamSpool
from app.edge.stream import spool
from app.edge.stream.forwarder import ForwarderState, StreamForwarder
from app.stream.kafka import ensure_topics
from tests.integration.conftest import db_session, make_tick

BROKER = "localhost:9092"


def _broker_up() -> bool:
    try:
        with socket.create_connection(("localhost", 9092), timeout=1):
            return True
    except OSError:
        return False


needs_kafka = pytest.mark.skipif(not _broker_up(), reason="Kafka is not running on localhost:9092")


@pytest.fixture
async def stream(conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    run = uuid.uuid4().hex[:8]
    settings = get_settings()
    values = {
        "kafka_enabled": True,
        "kafka_bootstrap_servers": BROKER,
        "kafka_topic_telemetry": f"safe2go.test.{run}.telemetry",
        "kafka_topic_events": f"safe2go.test.{run}.events",
        "kafka_consumer_group": f"safe2go-test-{run}",
        "kafka_retention_hours": 1,
        "stream_batch_size": 200,
        "stream_max_rate": 0.0,
        "stream_send_timeout_seconds": 15.0,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    async with db_session(conn) as session:
        for model in (StreamSpool, TelemetryArchive, TelemetryGap, StreamEvent, FleetMinuteRollup):
            await session.execute(delete(model))
        await session.commit()
    set_cloud_reachable(True)
    yield values


async def _count(conn: AsyncConnection, model: type) -> int:
    async with db_session(conn) as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _spool_ticks(conn: AsyncConnection, world: dict, n: int) -> None:
    async with db_session(conn) as session:
        for second in range(n):
            await spool.spool_tick(session, make_tick(world, second), str(uuid.uuid4()))
        await session.commit()


async def _consume_until(consumer: AnalyticsConsumer, done, timeout: float = 30.0) -> None:  # noqa: ANN001 - async predicate
    deadline = time.monotonic() + timeout
    assert await consumer._ensure_started()
    while time.monotonic() < deadline:
        await consumer.poll_once()
        if await done():
            return
    raise AssertionError("consumer did not catch up in time")


async def test_real_client_against_an_unreachable_broker_keeps_the_spool(
    conn: AsyncConnection, world: dict, stream: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "kafka_bootstrap_servers", "localhost:9")
    await _spool_ticks(conn, world, 5)
    forwarder = StreamForwarder(lambda: db_session(conn))
    try:
        started = time.monotonic()
        assert await forwarder.run_once() == 0
        assert time.monotonic() - started < 30, "an unreachable broker fails the pass, it does not hang"
        assert forwarder.stats.state == ForwarderState.BROKER_DOWN
        assert forwarder.stats.last_error
    finally:
        await forwarder.stop()
    assert await _count(conn, StreamSpool) == 5


@needs_kafka
async def test_end_to_end_through_kafka_with_resends_and_a_consumer_restart(
    conn: AsyncConnection, world: dict, stream: dict
) -> None:
    await _spool_ticks(conn, world, 150)
    forwarder = StreamForwarder(lambda: db_session(conn))
    try:
        for _ in range(20):
            await forwarder.run_once()
            if await _count(conn, StreamSpool) == 0:
                break
        assert await _count(conn, StreamSpool) == 0, forwarder.stats.last_error
        assert forwarder.stats.sent_total == 150
    finally:
        await forwarder.stop()

    consumer = AnalyticsConsumer(lambda: db_session(conn))
    try:
        async def all_stored() -> bool:
            return await _count(conn, TelemetryArchive) == 150
        await _consume_until(consumer, all_stored)
    finally:
        await consumer.stop()
    async with db_session(conn) as session:
        rolled = (await session.execute(select(func.sum(FleetMinuteRollup.ticks)))).scalar_one()
    assert rolled == 150

    # Resend every record (what a crash between ack and delete produces).
    producer = AIOKafkaProducer(bootstrap_servers=BROKER)
    await producer.start()
    try:
        reader = AIOKafkaConsumer(
            stream["kafka_topic_telemetry"], bootstrap_servers=BROKER, auto_offset_reset="earliest",
            enable_auto_commit=False, group_id=None,
        )
        await reader.start()
        try:
            originals = []
            deadline = time.monotonic() + 20
            while len(originals) < 150 and time.monotonic() < deadline:
                for records in (await reader.getmany(timeout_ms=1000)).values():
                    originals.extend(records)
        finally:
            await reader.stop()
        assert len(originals) == 150
        for record in originals:
            await producer.send_and_wait(stream["kafka_topic_telemetry"], value=record.value, key=record.key)
    finally:
        await producer.stop()

    restarted = AnalyticsConsumer(lambda: db_session(conn))
    try:
        async def resends_seen() -> bool:
            return restarted.stats.received_total >= 150
        await _consume_until(restarted, resends_seen)
    finally:
        await restarted.stop()
    assert restarted.stats.received_total == 150, "the restart resumed after the committed originals"
    assert restarted.stats.duplicates_total == 150
    assert await _count(conn, TelemetryArchive) == 150


@needs_kafka
async def test_topics_are_created_with_the_configured_partitions(stream: dict) -> None:
    await ensure_topics(get_settings())
    await ensure_topics(get_settings())  # idempotent
    admin = AIOKafkaAdminClient(bootstrap_servers=BROKER)
    await admin.start()
    try:
        described = await admin.describe_topics([stream["kafka_topic_telemetry"]])
    finally:
        await admin.close()
    assert len(described[0]["partitions"]) == get_settings().kafka_topic_partitions
