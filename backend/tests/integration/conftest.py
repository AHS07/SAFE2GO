"""Shared fixtures for DB integration tests.

Each test runs inside one outer transaction that is rolled back, so the
database is left unchanged. Tests are skipped when the database is not
reachable.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.cloud.publish import queue_training_content
from app.cloud.training.catalog import publish_catalog
from app.config.settings import get_settings
from app.core.clock import set_sim_time, sim_now
from app.core.security import create_access_token
from app.db.models.edge.assignment import EdgeMachine, EdgeOperator, EdgeShift, EdgeTask
from app.db.models.edge.behavior import Recommendation
from app.db.session import get_db
from app.edge.telemetry import state
from app.edge.telemetry.pipeline import EdgePipeline
from app.main import app
from app.schemas.telemetry import TelemetryTick
from app.sync.worker import deliver_all

T0 = datetime(2030, 1, 7, 8, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def conn() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(get_settings().database_url)
    try:
        connection = await engine.connect()
    except Exception as exc:  # noqa: BLE001 - any connection failure means skip
        await engine.dispose()
        pytest.skip(f"Database not reachable: {exc}")
    outer = await connection.begin()
    try:
        yield connection
    finally:
        await outer.rollback()
        await connection.close()
        await engine.dispose()
        state.clear()


def db_session(conn: AsyncConnection) -> AsyncSession:
    # Commits inside the code under test become savepoints of the outer transaction.
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


@pytest.fixture
async def world(conn: AsyncConnection) -> AsyncIterator[dict]:
    """Training content plus one operator, machine, shift, and in-progress task.

    The sim clock is set inside the shift, since the task is in progress.
    """
    saved_clock = sim_now()
    set_sim_time(T0 + timedelta(hours=1))
    async with db_session(conn) as session:
        await publish_catalog(session)
        await queue_training_content(session)
        await session.commit()
    # Training content reaches the edge through sync, as in the app.
    await deliver_all(lambda: db_session(conn))

    # Edge copies of the operator's shift, as sync would have delivered them.
    async with db_session(conn) as session:
        operator = EdgeOperator(
            operator_id=str(uuid.uuid4()),
            operator_name="Test Operator",
            user_id=str(uuid.uuid4()),
            username=f"test-{uuid.uuid4().hex[:8]}",
        )
        machine = EdgeMachine(
            machine_id=str(uuid.uuid4()),
            machine_model="TEST-EX",
            machine_type="excavator",
            machine_age=2,
            machine_status="available",
            service_interval_hours=500.0,
            last_service_engine_hours=100.0,
            bucket_capacity=1.2,
            tilt_limit_degrees=30.0,
            rated_max_rpm=2000.0,
        )
        session.add_all([operator, machine])
        await session.flush()
        shift = EdgeShift(
            shift_id=str(uuid.uuid4()),
            machine_id=machine.machine_id,
            operator_id=operator.operator_id,
            date=T0,
            scheduled_start=T0,
            scheduled_end=T0 + timedelta(hours=8),
            weather_forecast="clear",
            weather_actual="clear",
        )
        session.add(shift)
        await session.flush()
        task = EdgeTask(
            task_id=str(uuid.uuid4()),
            shift_id=shift.shift_id,
            task_type="excavation",
            operator_skill_at_assignment="intermediate",
            target_quantity=100.0,
            quantity_unit="m3",
            material_type="clay",
            status="in_progress",
            scheduled_start=T0,
            actual_start=T0,
        )
        session.add(task)
        await session.commit()

    try:
        yield {
            "operator_id": operator.operator_id,
            "machine_id": machine.machine_id,
            "shift_id": shift.shift_id,
            "task_id": task.task_id,
        }
    finally:
        set_sim_time(saved_clock)


def make_tick(world: dict, second: int, **overrides) -> TelemetryTick:
    raw = {
        "timestamp": T0 + timedelta(seconds=second),
        "machine_id": world["machine_id"],
        "operator_id": world["operator_id"],
        "task_id": world["task_id"],
        "shift_id": world["shift_id"],
        "engine_running": True,
        "engine_rpm": 1400.0,
        "engine_hours": 100.0,
        "fuel_used": 1.0,
        "hydraulic_active": True,
        "machine_speed": 0.0,
        "load_cycles": 0,
        "payload_pct": 50.0,
        "seatbelt_status": "fastened",
        "seat_occupied": True,
        "park_brake": True,
        "gear_state": "park",
        "ambient_temp": 25.0,
        "visibility": 400.0,
        "tilt_angle": 2.0,
        "proximity_sensor_ok": True,
    }
    raw.update(overrides)
    return TelemetryTick.model_validate(raw)


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, machine_id: str, message: dict) -> None:
        self.messages.append(message)

    def of_type(self, message_type: str) -> list[dict]:
        return [m["data"] for m in self.messages if m["type"] == message_type]


def _pipeline(conn: AsyncConnection, recorder: _Recorder) -> EdgePipeline:
    return EdgePipeline(lambda: db_session(conn), recorder)


async def _recommended_modules(conn: AsyncConnection, world: dict) -> list[str]:
    async with db_session(conn) as session:
        result = await session.execute(
            select(Recommendation.module_id)
            .where(Recommendation.operator_id == world["operator_id"])
            .where(Recommendation.shift_id == world["shift_id"])
        )
        return sorted(result.scalars().all())


@pytest.fixture
async def client(conn, world) -> AsyncIterator[AsyncClient]:
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with db_session(conn) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    token = create_access_token(
        user_id=str(uuid.uuid4()), role="operator", operator_id=world["operator_id"]
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


