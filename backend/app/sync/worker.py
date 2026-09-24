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
with exponential backoff (1, 2, 4 s, up to SYNC_BACKOFF_MAX_SECONDS).

Dead letters: a message that has kept failing for SYNC_DEAD_LETTER_AFTER_SECONDS
of real time (measured from its first failure, and only while the cloud link
is up, since nothing is attempted while it is down) is moved to the
direction's sync_dead_letter table. Later messages then continue. This is
the one exception to strict ordering, and it is visible: an ERROR is logged,
the admin Sync page lists the message, and Retry puts it back at the head of
its direction with the same idempotency key.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.core.clock import sim_now, wall_clock_now
from app.core.connectivity import is_cloud_reachable
from app.core.errors import ValidationError
from app.db.models.cloud.sync import CloudDeadLetter, CloudInbox, CloudOutbox
from app.db.models.edge.sync import EdgeDeadLetter, EdgeInbox, EdgeOutbox
from app.sync.handlers import cloud_to_edge, edge_to_cloud
from app.sync.outbox import decode_payload

log = logging.getLogger("safe2go.sync.worker")

_BATCH_SIZE = 200
_BUSY_WAIT_SECONDS = 0.2
_ERROR_TEXT_MAX = 500
_MESSAGE_FIELDS = (
    "message_id", "idempotency_key", "message_type", "entity_id", "payload",
    "created_at", "attempts", "last_error", "first_failed_at",
)
_DEAD_LETTER_FIELDS = (*_MESSAGE_FIELDS, "seq")


@dataclass(frozen=True)
class Direction:
    name: str
    outbox: type[CloudOutbox] | type[EdgeOutbox]
    inbox: type[CloudInbox] | type[EdgeInbox]
    dead_letter: type[CloudDeadLetter] | type[EdgeDeadLetter]
    handlers: dict


CLOUD_TO_EDGE = Direction("cloud_to_edge", CloudOutbox, EdgeInbox, CloudDeadLetter, cloud_to_edge.HANDLERS)
EDGE_TO_CLOUD = Direction("edge_to_cloud", EdgeOutbox, CloudInbox, EdgeDeadLetter, edge_to_cloud.HANDLERS)
DIRECTIONS = (CLOUD_TO_EDGE, EDGE_TO_CLOUD)


@dataclass
class DeliveryResult:
    delivered: int = 0
    duplicates: int = 0
    dead_lettered: int = 0
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
    effective_seq = message.replay_seq if message.replay_seq is not None else message.seq
    await handler(session, decode_payload(message.payload), effective_seq)
    session.add(direction.inbox(
        idempotency_key=message.idempotency_key,
        message_type=message.message_type,
        seq=effective_seq,
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
    A message that has failed for longer than the dead-letter window is moved
    aside and delivery continues with the next one.
    """
    result = DeliveryResult()
    async with session_factory() as session:
        ids = (
            await session.execute(
                select(direction.outbox.message_id)
                .where(direction.outbox.delivered_at.is_(None))
                .order_by(
                    direction.outbox.created_at,
                    func.coalesce(direction.outbox.replay_seq, direction.outbox.seq),
                )
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
                if await _record_failure(session, direction, message_id, exc):
                    result.dead_lettered += 1
                    continue
                result.failed_message_id = message_id
                return result
        if applied:
            result.delivered += 1
        else:
            result.duplicates += 1
    return result


def _dead_letter_window() -> timedelta:
    return timedelta(seconds=get_settings().sync_dead_letter_after_seconds)


async def _record_failure(session: AsyncSession, direction: Direction, message_id: str, exc: Exception) -> bool:
    """Record a failed attempt. Returns True when the message was dead-lettered."""
    message = await session.get(direction.outbox, message_id)
    if message is None:
        return False
    now = wall_clock_now()
    message.attempts = (message.attempts or 0) + 1
    message.last_error = f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_MAX]
    message.first_failed_at = message.first_failed_at or now
    details = {
        "direction": direction.name,
        "message_id": message_id,
        "message_type": message.message_type,
        "attempts": message.attempts,
        "error": message.last_error,
    }
    if now - message.first_failed_at < _dead_letter_window():
        await session.commit()
        log.warning("Sync delivery failed, will retry", extra=details)
        return False

    session.add(direction.dead_letter(
        **{name: getattr(message, name) for name in _DEAD_LETTER_FIELDS}, dead_at=now,
    ))
    await session.delete(message)
    await session.commit()
    log.error("Sync message moved to dead letters; later messages continue", extra=details)
    return True


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
            total.dead_lettered += batch.dead_lettered
            total.failed_message_id = batch.failed_message_id
            if batch.busy:
                # The app's worker is delivering this direction; wait for it to finish.
                await asyncio.sleep(_BUSY_WAIT_SECONDS)
                continue
            moved = batch.delivered + batch.duplicates + batch.dead_lettered
            if batch.failed_message_id is not None or moved == 0:
                break
        totals[direction.name] = total
    return totals


# ---------------------------------------------------------------------------
# Dead letters (admin)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadLetterView:
    message_id: str
    direction: str
    message_type: str
    entity_id: str | None
    attempts: int
    last_error: str | None
    created_at: datetime
    first_failed_at: datetime | None
    dead_at: datetime


async def list_dead_letters(session: AsyncSession) -> list[DeadLetterView]:
    views: list[DeadLetterView] = []
    for direction in DIRECTIONS:
        rows = (await session.execute(select(direction.dead_letter))).scalars().all()
        views.extend(
            DeadLetterView(
                message_id=r.message_id, direction=direction.name, message_type=r.message_type,
                entity_id=r.entity_id, attempts=r.attempts, last_error=r.last_error,
                created_at=r.created_at, first_failed_at=r.first_failed_at, dead_at=r.dead_at,
            )
            for r in rows
        )
    return sorted(views, key=lambda v: v.dead_at, reverse=True)


async def retry_dead_letter(session: AsyncSession, message_id: str) -> str | None:
    """Put a dead letter back in its outbox with a fresh retry window.

    It keeps its idempotency key and original timestamp, so it is delivered
    first in its direction and never applied twice. Returns the direction
    name, or None when no such dead letter exists. The caller commits.
    """
    for direction in DIRECTIONS:
        row = await session.get(direction.dead_letter, message_id)
        if row is None:
            continue
        if row.seq <= 0:
            raise ValidationError(
                "This dead letter predates sequence tracking and cannot be replayed safely. Recreate the latest entity update.",
                details={"message_id": message_id, "reason": "missing_original_sequence"},
            )
        values = {name: getattr(row, name) for name in _MESSAGE_FIELDS}
        values.update(attempts=0, first_failed_at=None, last_error=None, replay_seq=row.seq)
        await session.execute(delete(direction.dead_letter).where(direction.dead_letter.message_id == message_id))
        session.add(direction.outbox(**values))
        await session.flush()
        log.info("Dead letter queued for retry", extra={"direction": direction.name, "message_id": message_id})
        return direction.name
    return None


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
