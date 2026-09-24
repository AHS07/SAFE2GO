"""Edge forwarder: drains the spool to Kafka, paced and at-least-once.

Each pass:
  1. Enforce the spool limit (runs even while offline).
  2. Stop if the cloud link is down or the broker is in backoff.
  3. Read the oldest batch (gap markers, then safety events, then ticks).
  4. Send with acks=all and wait for the broker's acknowledgements.
  5. Delete only acknowledged records, per machine up to the first failure,
     so a failed record is resent before anything after it is lost.
  6. Sleep as needed so replay never exceeds STREAM_MAX_RATE.

A crash after the broker acknowledges but before the delete commits means
those records are sent again. Consumers drop them by event_id.

The forwarder never touches the tick path: safety keeps running whatever
the broker or link state.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.core.clock import wall_clock_now
from app.core.connectivity import is_cloud_reachable
from app.db.models.edge.stream import StreamSpool
from app.edge.stream import spool
from app.stream.codec import Envelope, encode, topic_for

log = logging.getLogger("safe2go.stream.forwarder")

SessionFactory = Callable[[], AsyncSession]
_RATE_WINDOW_SECONDS = 10.0


class Producer(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, topic: str, value: bytes, key: bytes, headers: list) -> Awaitable[Any]: ...


class ForwarderState(StrEnum):
    DISABLED = "disabled"
    STOPPED = "stopped"
    LINK_DOWN = "link_down"
    CONNECTING = "connecting"
    BROKER_DOWN = "broker_down"
    DRAINING = "draining"
    IDLE = "idle"


@dataclass
class ForwarderStats:
    state: ForwarderState = ForwarderState.STOPPED
    sent_total: int = 0
    failed_total: int = 0
    dropped_total: int = 0
    gaps_total: int = 0
    last_sent_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    retry_in_seconds: float | None = None
    send_rate: float = 0.0
    arrival_rate: float = 0.0


@dataclass
class _Rate:
    """Records per second over a sliding window of (time, count) samples."""
    samples: deque = field(default_factory=deque)

    def add(self, now: float, count: int) -> None:
        self.samples.append((now, count))
        while self.samples and now - self.samples[0][0] > _RATE_WINDOW_SECONDS:
            self.samples.popleft()

    def rate(self, now: float) -> float:
        while self.samples and now - self.samples[0][0] > _RATE_WINDOW_SECONDS:
            self.samples.popleft()
        if not self.samples:
            return 0.0
        # Divide by the time the samples cover (at least 1 s), not the full
        # window, so the rate is right in the first seconds after a start.
        span = max(1.0, min(_RATE_WINDOW_SECONDS, now - self.samples[0][0]))
        return sum(c for _, c in self.samples) / span


def acknowledged_prefix(rows: list[StreamSpool], results: list[object]) -> list[int]:
    """Seqs safe to delete: per machine, every record before its first failure."""
    blocked: set[str] = set()
    done: list[int] = []
    for row, result in zip(rows, results, strict=True):
        if row.machine_id in blocked:
            continue
        if isinstance(result, BaseException):
            blocked.add(row.machine_id)
            continue
        done.append(row.seq)
    return done


class StreamForwarder:
    def __init__(
        self,
        session_factory: SessionFactory,
        producer_factory: Callable[[Settings], Producer] | None = None,
        topics_ready: Callable[[Settings], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._producer_factory = producer_factory
        self._topics_ready = topics_ready
        self._sleep = sleep
        self._clock = clock
        self._producer: Producer | None = None
        self._topics_checked = False
        self._task: asyncio.Task | None = None
        self._attempts = 0
        self._retry_at = 0.0
        self._sent = _Rate()
        self._arrivals = _Rate()
        self._last_written = spool.written_total()
        self.stats = ForwarderStats()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="stream_forwarder")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_producer()
        self.stats.state = ForwarderState.STOPPED

    async def _loop(self) -> None:
        poll = get_settings().stream_poll_seconds
        while True:
            try:
                sent = await self.run_once()
            except Exception as exc:
                # A database outage must not kill the forwarder; the next pass retries.
                sent = 0
                self._record_error(f"Forwarder pass failed: {exc}")
                log.error("Forwarder pass failed", extra={"error": str(exc)[:200]}, exc_info=exc)
            if sent == 0:
                await self._sleep(poll)

    # -- one pass ----------------------------------------------------------

    async def run_once(self) -> int:
        """One forwarding pass. Returns the number of records acknowledged."""
        settings = get_settings()
        now = self._clock()
        self._sample_arrivals(now)
        self._update_rates(now)

        async with self._session_factory() as session:
            gaps = await spool.enforce_limit(
                session, settings.stream_spool_max_records, settings.stream_spool_low_water
            )
            await session.commit()
        if gaps:
            self.stats.gaps_total += len(gaps)
            self.stats.dropped_total += sum(g.dropped_count for g in gaps)

        if not is_cloud_reachable():
            self.stats.state = ForwarderState.LINK_DOWN
            return 0
        if now < self._retry_at:
            self.stats.retry_in_seconds = round(self._retry_at - now, 1)
            return 0
        self.stats.retry_in_seconds = None

        if not await self._ensure_producer(settings):
            return 0

        async with self._session_factory() as session:
            rows = list((
                await session.execute(
                    select(StreamSpool)
                    .order_by(StreamSpool.priority, StreamSpool.seq)
                    .limit(settings.stream_batch_size)
                )
            ).scalars().all())
        if not rows:
            self.stats.state = ForwarderState.IDLE
            return 0

        self.stats.state = ForwarderState.DRAINING
        started = self._clock()
        results = await self._send(settings, rows)
        acked = acknowledged_prefix(rows, results)
        failures = [r for r in results if isinstance(r, BaseException)]

        if acked:
            async with self._session_factory() as session:
                await session.execute(delete(StreamSpool).where(StreamSpool.seq.in_(acked)))
                await session.commit()
            self.stats.sent_total += len(acked)
            self.stats.last_sent_at = wall_clock_now()
            self._sent.add(self._clock(), len(acked))

        if failures:
            self.stats.failed_total += len(rows) - len(acked)
            self._record_error(f"Kafka did not acknowledge {len(failures)} of {len(rows)} records: {failures[0]}")
            log.warning(
                "Kafka send failed, unacknowledged records stay in the spool",
                extra={"sent": len(acked), "kept": len(rows) - len(acked), "error": str(failures[0])[:200]},
            )
            await self._close_producer()
            self._schedule_retry()
        else:
            self._attempts = 0

        await self._pace(settings, len(rows), started)
        self._update_rates(self._clock())
        return len(acked)

    async def _send(self, settings: Settings, rows: list[StreamSpool]) -> list[object]:
        """Send a batch and wait for every acknowledgement (or failure)."""
        producer = self._producer
        assert producer is not None

        async def send_all() -> list[object]:
            pending = []
            for row in rows:
                envelope = Envelope(
                    event_id=row.event_id,
                    kind=row.kind,
                    edge_seq=row.seq,
                    machine_id=row.machine_id,
                    event_time=row.event_time,
                    spooled_at=row.spooled_at,
                    data=row.payload,
                )
                pending.append(await producer.send(
                    topic_for(row.kind, settings.kafka_topic_telemetry, settings.kafka_topic_events),
                    value=encode(envelope),
                    key=row.machine_id.encode(),
                    headers=[("event_id", row.event_id.encode())],
                ))
            return list(await asyncio.gather(*pending, return_exceptions=True))

        try:
            return await asyncio.wait_for(send_all(), timeout=settings.stream_send_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - timeout or send error: nothing counts as acknowledged
            return [exc] * len(rows)

    async def _pace(self, settings: Settings, count: int, started: float) -> None:
        """Keep replay at or below STREAM_MAX_RATE records per second."""
        if settings.stream_max_rate <= 0:
            return
        minimum = count / settings.stream_max_rate
        elapsed = self._clock() - started
        if elapsed < minimum:
            await self._sleep(minimum - elapsed)

    # -- producer ----------------------------------------------------------

    async def _ensure_producer(self, settings: Settings) -> bool:
        if self._producer is not None:
            return True
        self.stats.state = ForwarderState.CONNECTING
        factory = self._producer_factory
        topics_ready = self._topics_ready
        if factory is None or topics_ready is None:
            from app.stream.kafka import ensure_topics, make_producer

            factory = factory or make_producer
            topics_ready = topics_ready or ensure_topics
        producer = factory(settings)
        try:
            await producer.start()
            if not self._topics_checked:
                await topics_ready(settings)
                self._topics_checked = True
        except Exception as exc:  # noqa: BLE001 - broker unreachable: keep spooling, retry later
            with contextlib.suppress(Exception):
                await producer.stop()
            self._record_error(f"Kafka unreachable: {exc}")
            log.warning("Kafka unreachable, records stay in the spool", extra={"error": str(exc)[:200]})
            self._schedule_retry()
            return False
        self._producer = producer
        log.info("Kafka producer connected", extra={"bootstrap": settings.kafka_bootstrap_servers})
        return True

    async def _close_producer(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            with contextlib.suppress(Exception):
                await producer.stop()

    def _schedule_retry(self) -> None:
        self._attempts += 1
        delay = min(2.0 ** (self._attempts - 1), get_settings().stream_backoff_max_seconds)
        self._retry_at = self._clock() + delay
        self.stats.state = ForwarderState.BROKER_DOWN
        self.stats.retry_in_seconds = round(delay, 1)

    def _record_error(self, message: str) -> None:
        self.stats.last_error = message[:300]
        self.stats.last_error_at = wall_clock_now()

    # -- rates -------------------------------------------------------------

    def _sample_arrivals(self, now: float) -> None:
        written = spool.written_total()
        self._arrivals.add(now, max(0, written - self._last_written))
        self._last_written = written

    def _update_rates(self, now: float) -> None:
        self.stats.send_rate = round(self._sent.rate(now), 1)
        self.stats.arrival_rate = round(self._arrivals.rate(now), 1)


def drain_eta_seconds(backlog: int, stats: ForwarderStats) -> float | None:
    """Seconds until the spool is empty at current rates; None if it is not shrinking."""
    if backlog == 0:
        return 0.0
    if stats.state not in (ForwarderState.DRAINING, ForwarderState.IDLE):
        return None
    net = stats.send_rate - stats.arrival_rate
    if net <= 0:
        return None
    return round(backlog / net, 1)
