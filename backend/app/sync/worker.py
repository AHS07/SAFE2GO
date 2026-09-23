"""Sync worker: delivers outbox messages between the tiers.

Runs only while cloud_reachable is true. Each direction is delivered in
order (sim timestamp, then outbox sequence). A message is applied, its
idempotency key recorded in the receiving inbox, and the outbox row marked
delivered, all in one transaction. A key already in the inbox means a
duplicate delivery: the message is marked delivered with no other effect.

Delivery takes a row lock on each outbox message, so the app's worker and
a script (seed_demo) can run at the same time without applying a message
twice or out of order.

A failing message stays in the outbox with attempts and last_error, and
blocks later messages in its direction so order is kept. It is retried
with exponential backoff (1, 2, 4 s, up to the configured ceiling).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.core.clock import sim_now
from app.core.connectivity import is_cloud_reachable
from app.db.models.cloud.sync import CloudInbox, CloudOutbox
from app.db.models.edge.sync import EdgeInbox, EdgeOutbox
from app.sync.handlers import cloud_to_edge, edge_to_cloud
from app.sync.outbox import decode_payload

log = logging.getLogger("safe2go.sync.worker")

_BATCH_SIZE = 200
_BUSY_WAIT_SECONDS = 0.2
_ERROR_TEXT_MAX = 500


@dataclass(frozen=True)
class Direction:
    name: str
    outbox: type[CloudOutbox] | type[EdgeOutbox]
    inbox: type[CloudInbox] | type[EdgeInbox]
    handlers: dict


CLOUD_TO_EDGE = Direction("cloud_to_edge", CloudOutbox, EdgeInbox, cloud_to_edge.HANDLERS)
EDGE_TO_CLOUD = Direction("edge_to_cloud", EdgeOutbox, CloudInbox, edge_to_cloud.HANDLERS)
DIRECTIONS = (CLOUD_TO_EDGE, EDGE_TO_CLOUD)


@dataclass
class DeliveryResult:
    delivered: int = 0
    duplicates: int = 0
    failed_message_id: str | None = None
    busy: bool = False          # another deliverer holds the next message


async def pending_count(session: AsyncSession, direction: Direction) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(direction.outbox).where(direction.outbox.delivered_at.is_(None))
        )
    ).scalar_one()


async def _apply(session: AsyncSession, direction: Direction, message) -> bool:  # noqa: ANN001 - outbox row
    """Apply one message. Returns False when it was a duplicate."""
    if await session.get(direction.inbox, message.idempotency_key) is not None:
        message.delivered_at = sim_now()
        return False
    handler = direction.handlers.get(message.message_type)
    if handler is None:
        raise ValueError(f"No handler for message type {message.message_type}")
    await handler(session, decode_payload(message.payload), message.seq)
    session.add(direction.inbox(
        idempotency_key=message.idempotency_key,
        message_type=message.message_type,
        seq=message.seq,
        applied_at=sim_now(),
    ))
    message.delivered_at = sim_now()
    return True


async def deliver_pending(
    session_factory: Callable[[], AsyncSession],
    direction: Direction,
    ready: Callable[[str], bool] = lambda _message_id: True,
) -> DeliveryResult:
    """Deliver undelivered messages in order until done or one fails.

    ready(message_id) lets the worker hold back a message still in backoff.
    """
    result = DeliveryResult()
    async with session_factory() as session:
        ids = (
            await session.execute(
                select(direction.outbox.message_id)
                .where(direction.outbox.delivered_at.is_(None))
                .order_by(direction.outbox.created_at, direction.outbox.seq)
                .limit(_BATCH_SIZE)
            )
        ).scalars().all()

    for message_id in ids:
        if not ready(message_id):
            result.failed_message_id = message_id
            return result
        async with session_factory() as session:
            # Row lock: another deliverer (a script next to the running app) may
            # hold this message. Stop rather than skip ahead, so order is kept.
            message = await session.get(
                direction.outbox, message_id, with_for_update={"skip_locked": True}
            )
            if message is None:
                result.busy = True
                return result
            if message.delivered_at is not None:
                continue
            try:
                applied = await _apply(session, direction, message)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                await _record_failure(session_factory, direction, message_id, exc)
                result.failed_message_id = message_id
                return result
        if applied:
            result.delivered += 1
        else:
            result.duplicates += 1
    return result


async def _record_failure(
    session_factory: Callable[[], AsyncSession], direction: Direction, message_id: str, exc: Exception
) -> None:
    async with session_factory() as session:
        message = await session.get(direction.outbox, message_id)
        message.attempts = (message.attempts or 0) + 1
        message.last_error = f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_MAX]
        await session.commit()
        log.warning(
            "Sync delivery failed, will retry",
            extra={
                "direction": direction.name,
                "message_id": message_id,
                "message_type": message.message_type,
                "attempts": message.attempts,
                "error": message.last_error,
            },
        )


async def deliver_all(session_factory: Callable[[], AsyncSession]) -> dict[str, DeliveryResult]:
    """Deliver everything pending in both directions, stopping a direction at its first failure.

    Used by scripts and tests; the running app uses SyncWorker.
    """
    totals: dict[str, DeliveryResult] = {}
    for direction in DIRECTIONS:
        total = DeliveryResult()
        while True:
            batch = await deliver_pending(session_factory, direction)
            total.delivered += batch.delivered
            total.duplicates += batch.duplicates
            total.failed_message_id = batch.failed_message_id
            if batch.busy:
                # The app's worker is delivering this direction; wait for it to finish.
                await asyncio.sleep(_BUSY_WAIT_SECONDS)
                continue
            if batch.failed_message_id is not None or batch.delivered + batch.duplicates == 0:
                break
        totals[direction.name] = total
    return totals


class SyncWorker:
    """Background loop. Retry timing is real time, so it uses the event loop clock."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._retry_at: dict[str, float] = {}
        self._attempts: dict[str, int] = {}
        self._last_run: datetime | None = None

    @property
    def last_run(self) -> datetime | None:
        return self._last_run

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="sync_worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _ready(self, message_id: str) -> bool:
        return asyncio.get_running_loop().time() >= self._retry_at.get(message_id, 0.0)

    def _schedule_retry(self, message_id: str) -> None:
        attempts = self._attempts.get(message_id, 0) + 1
        self._attempts[message_id] = attempts
        delay = min(2.0 ** (attempts - 1), get_settings().sync_backoff_max_seconds)
        self._retry_at[message_id] = asyncio.get_running_loop().time() + delay

    async def run_once(self) -> None:
        if not is_cloud_reachable():
            return
        for direction in DIRECTIONS:
            result = await deliver_pending(self._session_factory, direction, self._ready)
            failed = result.failed_message_id
            if failed is not None and self._ready(failed):
                self._schedule_retry(failed)
        self._last_run = sim_now()

    async def _loop(self) -> None:
        poll = get_settings().sync_poll_seconds
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                # A database outage must not kill the worker; the next pass retries.
                log.error("Sync pass failed", extra={"error": str(exc)}, exc_info=exc)
            await asyncio.sleep(poll)
