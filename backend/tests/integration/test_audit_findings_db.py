"""Fixes from the session 12 audit, against the database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config.settings import get_settings
from app.core.clock import sim_now
from app.core.security import create_access_token
from app.db.models.cloud.sync import CloudDeadLetter, CloudOutbox
from app.db.models.edge.assignment import EdgeShift, EdgeTask
from app.db.models.edge.incident import Incident
from app.edge.shift_summary import build_shift_summary
from app.edge.tasks.service import update_progress
from app.edge.telemetry.pipeline import EdgePipeline
from app.sync.worker import CLOUD_TO_EDGE, deliver_pending, pending_count
from simulator.live.clock_driver import ClockDriver
from tests.integration.conftest import _Recorder, db_session, make_tick


class _FlakyFactory:
    """Session factory whose next commit can be made to fail (a database fault)."""

    def __init__(self, conn: AsyncConnection) -> None:
        self.conn = conn
        self.fail_next_commit = False

    def __call__(self):  # noqa: ANN204 - AsyncSession
        session = db_session(self.conn)
        if self.fail_next_commit:
            self.fail_next_commit = False

            async def broken_commit() -> None:
                raise RuntimeError("database unavailable")
            session.commit = broken_commit
        return session


async def _incidents(conn: AsyncConnection, world: dict, incident_type: str) -> list[Incident]:
    async with db_session(conn) as session:
        return list((await session.execute(
            select(Incident).where(Incident.shift_id == world["shift_id"], Incident.incident_type == incident_type)
        )).scalars().all())


# ---------------------------------------------------------------------------
# Finding 1: engine state never runs ahead of the database
# ---------------------------------------------------------------------------


async def test_a_rolled_back_tick_leaves_no_phantom_incident(conn: AsyncConnection, world: dict) -> None:
    factory, sent = _FlakyFactory(conn), _Recorder()
    pipeline = EdgePipeline(factory, sent)
    for second in range(2):
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))

    factory.fail_next_commit = True               # the tick that opens the incident fails to commit
    await pipeline.process(make_tick(world, 2, proximity_distance=3.0))
    assert await _incidents(conn, world, "proximity") == []
    assert sent.of_type("incident") == [], "nothing is announced for a rolled-back tick"

    await pipeline.process(make_tick(world, 3, proximity_distance=3.0))
    incidents = await _incidents(conn, world, "proximity")
    assert len(incidents) == 1
    assert [m["event"] for m in sent.of_type("incident")] == ["opened"]


# ---------------------------------------------------------------------------
# Finding 5: auto-slow holds until the incident is resolved
# ---------------------------------------------------------------------------


async def test_auto_slow_holds_until_a_cleared_critical_is_acknowledged(client, conn: AsyncConnection, world: dict) -> None:
    driver = ClockDriver(tick_seconds=1.0, selected_speed=20)
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder(), clock_driver=driver)
    second = 0
    for _ in range(4):                            # proximity at 3 m: CRITICAL
        await pipeline.process(make_tick(world, second, proximity_distance=3.0))
        second += 1
    assert driver.effective_speed == 1
    for _ in range(4):                            # hazard gone and the clear confirmed
        await pipeline.process(make_tick(world, second, proximity_distance=20.0))
        second += 1
    incident = (await _incidents(conn, world, "proximity"))[0]
    assert incident.event_end is not None and incident.status == "open"
    assert driver.effective_speed == 1, "a cleared CRITICAL is still unresolved"

    response = await client.post(f"/api/operator/incidents/{incident.incident_id}/acknowledge")
    assert response.json()["status"] == "resolved"
    assert driver.effective_speed == 20


async def test_a_cleared_warning_resolves_and_ends_auto_slow(conn: AsyncConnection, world: dict) -> None:
    driver = ClockDriver(tick_seconds=1.0, selected_speed=10)
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder(), clock_driver=driver)
    second = 0
    for _ in range(4):                            # 8 m: WARNING only
        await pipeline.process(make_tick(world, second, proximity_distance=8.0))
        second += 1
    assert driver.effective_speed == 1
    for _ in range(4):
        await pipeline.process(make_tick(world, second, proximity_distance=20.0))
        second += 1
    assert (await _incidents(conn, world, "proximity"))[0].status == "resolved"
    assert driver.effective_speed == 10


# ---------------------------------------------------------------------------
# Finding 6: every cycle in a tick counts
# ---------------------------------------------------------------------------


async def test_a_tick_with_several_cycles_counts_each(conn: AsyncConnection, world: dict) -> None:
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    await pipeline.process(make_tick(world, 0, load_cycles=3, cycle_payload_pct=100.0))
    async with db_session(conn) as session:
        task = await session.get(EdgeTask, world["task_id"])
    assert task.completed_quantity == pytest.approx(3 * 1.2)   # three full 1.2 m3 buckets


# ---------------------------------------------------------------------------
# Finding 4: summary time follows tick gaps, like the behavior engine
# ---------------------------------------------------------------------------


async def test_shift_summary_uses_the_gap_between_ticks(conn: AsyncConnection, world: dict) -> None:
    pipeline = EdgePipeline(lambda: db_session(conn), _Recorder())
    for second in range(0, 20, 2):                # ten ticks, two seconds apart
        await pipeline.process(make_tick(world, second, hydraulic_active=False))
    async with db_session(conn) as session:
        summary = await build_shift_summary(session, world["shift_id"])
    # First tick counts as one nominal second, the other nine as two seconds each.
    assert summary["engine_hours"] == pytest.approx(19 / 3600, abs=1e-3)
    assert summary["idle_ratio"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Finding 3 / I-23 to I-26: dead letters
# ---------------------------------------------------------------------------


async def _queue(conn: AsyncConnection, message_type: str, payload: str) -> str:
    async with db_session(conn) as session:
        message = CloudOutbox(
            message_id=str(uuid.uuid4()), idempotency_key=f"{message_type}:{uuid.uuid4().hex}",
            message_type=message_type, payload=payload, created_at=sim_now(), attempts=0,
        )
        session.add(message)
        await session.commit()
        return message.message_id


@pytest.fixture
def dead_letter_window(monkeypatch):  # noqa: ANN001, ANN201 - pytest fixture
    def set_window(seconds: float) -> None:
        monkeypatch.setattr(get_settings(), "sync_dead_letter_after_seconds", seconds)
    return set_window


async def test_a_failing_message_blocks_its_direction_within_the_window(
    conn: AsyncConnection, world: dict, dead_letter_window
) -> None:
    dead_letter_window(3600)
    poison = await _queue(conn, "not_a_type", "{}")
    good = await _queue(conn, "task_cancelled", '{"task_id": "unknown"}')
    result = await deliver_pending(lambda: db_session(conn), CLOUD_TO_EDGE)
    assert result.failed_message_id == poison and result.dead_lettered == 0
    async with db_session(conn) as session:
        assert (await session.get(CloudOutbox, good)).delivered_at is None
        assert (await session.get(CloudOutbox, poison)).first_failed_at is not None


async def test_after_the_window_a_message_moves_to_dead_letters_and_order_resumes(
    conn: AsyncConnection, world: dict, dead_letter_window
) -> None:
    dead_letter_window(0)
    poison = await _queue(conn, "not_a_type", "{}")
    good = await _queue(conn, "task_cancelled", '{"task_id": "unknown"}')
    result = await deliver_pending(lambda: db_session(conn), CLOUD_TO_EDGE)
    assert result.dead_lettered == 1 and result.delivered >= 1
    async with db_session(conn) as session:
        assert await session.get(CloudOutbox, poison) is None
        dead = await session.get(CloudDeadLetter, poison)
        assert dead is not None and "No handler" in dead.last_error
        assert (await session.get(CloudOutbox, good)).delivered_at is not None
        assert await pending_count(session, CLOUD_TO_EDGE) == 0, "a dead letter no longer counts as queued"


async def test_admin_lists_and_retries_a_dead_letter(conn: AsyncConnection, world: dict, dead_letter_window) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.db.models.cloud.user import User
    from app.db.session import get_db
    from app.main import app

    dead_letter_window(0)
    poison = await _queue(conn, "not_a_type", "{}")
    await deliver_pending(lambda: db_session(conn), CLOUD_TO_EDGE)

    async with db_session(conn) as session:
        admin = User(user_id=str(uuid.uuid4()), role="admin", username=f"adm-{uuid.uuid4().hex[:8]}", password_hash="x")
        session.add(admin)
        await session.commit()

    async def _get_db():  # noqa: ANN202
        async with db_session(conn) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    headers = {"Authorization": f"Bearer {create_access_token(user_id=admin.user_id, role='admin')}"}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as http:
            listed = (await http.get("/api/admin/sync/dead-letters")).json()
            assert [d["message_id"] for d in listed if d["message_id"] == poison] == [poison]
            assert next(d for d in listed if d["message_id"] == poison)["direction"] == "cloud_to_edge"
            retried = await http.post(f"/api/admin/sync/dead-letters/{poison}/retry")
            assert retried.json() == {"message_id": poison, "direction": "cloud_to_edge"}
            missing = await http.post(f"/api/admin/sync/dead-letters/{uuid.uuid4()}/retry")
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)

    async with db_session(conn) as session:
        back = await session.get(CloudOutbox, poison)
        assert back is not None and back.attempts == 0 and back.first_failed_at is None
        assert await session.get(CloudDeadLetter, poison) is None


# ---------------------------------------------------------------------------
# I-27: the ETA revision reason is stored and shown
# ---------------------------------------------------------------------------


async def test_revision_reason_is_stored_and_returned(client, conn: AsyncConnection, world: dict) -> None:
    async with db_session(conn) as session:
        shift = await session.get(EdgeShift, world["shift_id"])
        shift.weather_forecast, shift.weather_actual = "clear", "rain"
        task = await session.get(EdgeTask, world["task_id"])
        task.raw_predicted_time, task.planning_eta = 60.0, 75.0
        await session.commit()
    async with db_session(conn) as session:
        trigger = await update_progress(world["task_id"], 30.0, session)
        await session.commit()
    assert trigger == "weather"

    tasks = (await client.get("/api/operator/tasks")).json()
    mine = next(t for t in tasks if t["task_id"] == world["task_id"])
    assert mine["eta_revision_reason"] == "weather"
    assert mine["eta_minutes"] is not None
