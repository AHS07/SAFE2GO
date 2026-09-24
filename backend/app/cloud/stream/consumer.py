"""Cloud analytics consumer: Kafka to the analytics tables.

Reads both topics in one consumer group. Each batch is stored in one
database transaction and the Kafka offsets are committed only after that
commit, so a crash in between replays the batch; storage ignores the
duplicates. A record that cannot be decoded is counted, logged, and
skipped, so it never blocks the partition.

It is cloud-internal, so the demo link toggle (edge to cloud) does not
pause it. A broker outage does: it reconnects with backoff.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.stream.analytics import apply_records
from app.config.settings import Settings, get_settings
from app.core.clock import wall_clock_now
from app.stream.codec import MalformedRecord, decode

log = logging.getLogger("safe2go.stream.consumer")

SessionFactory = Callable[[], AsyncSession]
_BATCH_MAX_RECORDS = 1000
_POLL_TIMEOUT_MS = 1000


class ConsumerState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    BROKER_DOWN = "broker_down"
    RUNNING = "running"


@dataclass
class ConsumerStats:
    state: ConsumerState = ConsumerState.STOPPED
    received_total: int = 0
    stored_total: int = 0
    duplicates_total: int = 0
    malformed_total: int = 0
    lag: int | None = None
    last_batch_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


class AnalyticsConsumer:
    def __init__(
        self,
        session_factory: SessionFactory,
        consumer_factory: Callable[[Settings], Any] | None = None,
        topics_ready: Callable[[Settings], Any] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._consumer_factory = consumer_factory
        self._topics_ready = topics_ready
        self._sleep = sleep
        self._consumer: Any = None
        self._task: asyncio.Task | None = None
        self._attempts = 0
        self.stats = ConsumerStats()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="stream_consumer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close()
        self.stats.state = ConsumerState.STOPPED

    async def _loop(self) -> None:
        while True:
            try:
                if not await self._ensure_started():
                    await self._sleep(self._backoff())
                    continue
                await self.poll_once()
                self._attempts = 0
            except Exception as exc:
                # Database or broker failure: offsets were not committed, so
                # the batch is read again after reconnecting.
                self._record_error(f"Consumer pass failed: {exc}")
                log.error("Consumer pass failed", extra={"error": str(exc)[:200]}, exc_info=exc)
                await self._close()
                self.stats.state = ConsumerState.BROKER_DOWN
                await self._sleep(self._backoff())

    async def _ensure_started(self) -> bool:
        if self._consumer is not None:
            return True
        settings = get_settings()
        self.stats.state = ConsumerState.CONNECTING
        factory, topics_ready = self._consumer_factory, self._topics_ready
        if factory is None or topics_ready is None:
            from app.stream.kafka import ensure_topics, make_consumer

            factory = factory or make_consumer
            topics_ready = topics_ready or ensure_topics
        consumer = None
        try:
            await topics_ready(settings)
            consumer = factory(settings)
            await consumer.start()
        except Exception as exc:  # noqa: BLE001 - broker unreachable: retry with backoff
            if consumer is not None:
                with contextlib.suppress(Exception):
                    await consumer.stop()
            self._record_error(f"Kafka unreachable: {exc}")
            log.warning("Analytics consumer cannot reach Kafka", extra={"error": str(exc)[:200]})
            self.stats.state = ConsumerState.BROKER_DOWN
            return False
        self._consumer = consumer
        self.stats.state = ConsumerState.RUNNING
        log.info("Analytics consumer connected", extra={"group": settings.kafka_consumer_group})
        return True

    async def poll_once(self) -> int:
        """Read, store, and commit one batch. Returns the records received."""
        consumer = self._consumer
        batches = await consumer.getmany(timeout_ms=_POLL_TIMEOUT_MS, max_records=_BATCH_MAX_RECORDS)
        records = [record for partition_records in batches.values() for record in partition_records]
        if records:
            envelopes = []
            malformed = 0
            for record in records:
                try:
                    envelopes.append(decode(record.value))
                except MalformedRecord as exc:
                    malformed += 1
                    log.warning(
                        "Undecodable Kafka record skipped",
                        extra={"topic": record.topic, "partition": record.partition, "offset": record.offset,
                               "reason": str(exc)[:200]},
                    )
            async with self._session_factory() as session:
                result = await apply_records(session, envelopes)
                await session.commit()
            await consumer.commit()
            self.stats.received_total += len(records)
            self.stats.stored_total += result.stored
            self.stats.duplicates_total += result.duplicates
            self.stats.malformed_total += malformed + result.malformed
            self.stats.last_batch_at = wall_clock_now()
        self.stats.lag = await self._lag()
        return len(records)

    async def _lag(self) -> int | None:
        """Records on the broker not yet read by this group (None until known)."""
        consumer = self._consumer
        total = 0
        try:
            for tp in consumer.assignment():
                highwater = consumer.highwater(tp)
                if highwater is None:
                    return None
                total += max(0, highwater - await consumer.position(tp))
        except Exception:  # noqa: BLE001 - lag is informational
            return None
        return total

    async def _close(self) -> None:
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            with contextlib.suppress(Exception):
                await consumer.stop()

    def _backoff(self) -> float:
        self._attempts += 1
        return min(2.0 ** (self._attempts - 1), get_settings().stream_backoff_max_seconds)

    def _record_error(self, message: str) -> None:
        self.stats.last_error = message[:300]
        self.stats.last_error_at = wall_clock_now()
