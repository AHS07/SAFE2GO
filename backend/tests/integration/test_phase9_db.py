"""Phase 9 against the database: hour meter from synced summaries, service
suggestions on both tiers, and the shift summary for operator and admin."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.security import create_access_token
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.sync import CloudOutbox
from app.db.models.edge.assignment import EdgeMachine, EdgeTask
from app.db.session import get_db
from app.edge.telemetry import state
from app.shared.enums import SyncMessageType
from app.sync.handlers.edge_to_cloud import apply_shift_summary
from app.sync.worker import deliver_all
from tests.integration.conftest import T0, db_session, make_tick
from tests.integration.test_sync_db import site  # noqa: F401 - fixture reused

H = timedelta(hours=1)


@pytest.fixture
async def api(conn: AsyncConnection, site: dict) -> AsyncIterator[AsyncClient]:  # noqa: F811 - uses the imported fixture
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with db_session(conn) as session:
            yield session

    from app.main import app

    app.dependency_overrides[get_db] = _get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


def _headers(site: dict, role: str = "operator") -> dict:  # noqa: F811
    if role == "admin":
        token = create_access_token(user_id=site["admin_id"], role="admin")
    else:
        token = create_access_token(user_id=site["users"][0], role="operator", operator_id=site["ops"][0])
    return {"Authorization": f"Bearer {token}"}


def _summary(shift_id: str, engine_hours: float) -> dict:
    return {"shift_id": shift_id, "engine_hours": engine_hours, "idle_minutes": 30.0, "idle_ratio": 0.25,
            "cycle_count": 40, "completed_quantity": 20.0, "fuel_used": 30.0, "cycle_rate": 20.0}


async def test_shift_summary_moves_the_hour_meter_once(conn: AsyncConnection, site: dict) -> None:  # noqa: F811
    machine_id = site["machines"][0]
    async with db_session(conn) as session:
        before = (await session.get(Machine, machine_id)).engine_hours
        await apply_shift_summary(session, _summary(site["shifts"][0], 2.0), 10)
        await apply_shift_summary(session, _summary(site["shifts"][0], 2.0), 11)   # same summary again
        await apply_shift_summary(session, _summary(site["shifts"][0], 2.5), 12)   # corrected upward
        await session.commit()
        after = (await session.get(Machine, machine_id)).engine_hours
        queued = (await session.execute(select(func.count()).select_from(CloudOutbox).where(
            CloudOutbox.message_type == SyncMessageType.MACHINE_MASTER.value, CloudOutbox.delivered_at.is_(None),
        ))).scalar_one()
    assert after == pytest.approx(before + 2.5)
    assert queued >= 1, "the new meter reading is sent back to the edge"

    await deliver_all(lambda: db_session(conn))
    async with db_session(conn) as session:
        assert (await session.get(EdgeMachine, machine_id)).engine_hours == pytest.approx(before + 2.5)


async def _set_meter(conn: AsyncConnection, machine_id: str, since_service: float) -> None:
    async with db_session(conn) as session:
        for model in (Machine, EdgeMachine):
            machine = await session.get(model, machine_id)
            machine.last_service_engine_hours = 1000.0
            machine.service_interval_hours = 500.0
            machine.engine_hours = 1000.0 + since_service
        await session.commit()


async def test_operator_sees_service_due_from_the_live_meter(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:  # noqa: F811
    machine_id = site["machines"][0]
    await _set_meter(conn, machine_id, 300.0)
    assert (await api.get("/api/operator/maintenance", headers=_headers(site))).json()["status"] == "ok"

    # A live tick carries a newer meter reading than the synced machine list.
    world = {"machine_id": machine_id, "operator_id": site["ops"][0], "task_id": None, "shift_id": site["shifts"][0]}
    state.record_tick(make_tick(world, 0, engine_hours=1460.0))
    body = (await api.get("/api/operator/maintenance", headers=_headers(site))).json()
    assert body["status"] == "due_soon" and body["hours_remaining"] == 40.0
    assert "Service due in 40 engine hours" in body["message"]


async def test_admin_machine_list_shows_service_state(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:  # noqa: F811
    await _set_meter(conn, site["machines"][1], 530.0)
    machines = (await api.get("/api/admin/machines", headers=_headers(site, "admin"))).json()
    mine = next(m for m in machines if m["machine_id"] == site["machines"][1])
    assert mine["service"]["status"] == "overdue"


async def test_operator_shift_summary(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:  # noqa: F811
    task_id = site["tasks"][0]
    async with db_session(conn) as session:
        task = await session.get(EdgeTask, task_id)
        task.status, task.actual_start, task.actual_end = "done", T0 + H, T0 + 2 * H
        task.completed_quantity, task.paused_minutes = 30.0, 12.0
        await session.commit()
    await api.post("/api/operator/incidents", headers=_headers(site), json={"description": "Loose gravel near ramp"})

    body = (await api.get("/api/operator/shift/summary", headers=_headers(site))).json()
    assert body["source"] == "edge"
    assert (body["tasks_done"], body["tasks_total"]) == (1, 1)
    assert body["tasks"][0]["working_minutes"] == 48.0
    assert body["incidents"] == [{"type": "manual_report", "count": 1}]
    assert body["time"] is None, "no telemetry yet for this shift"


async def test_admin_shift_summary_uses_synced_records(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:  # noqa: F811
    await api.post("/api/operator/incidents", headers=_headers(site), json={"description": "Loose gravel near ramp"})
    async with db_session(conn) as session:
        await apply_shift_summary(session, _summary(site["shifts"][0], 2.0), 50)
        await session.commit()

    before_sync = (await api.get(f"/api/admin/shifts/{site['shifts'][0]}/summary", headers=_headers(site, "admin"))).json()
    assert before_sync["incidents_total"] == 0, "the cloud only knows what has synced"

    await deliver_all(lambda: db_session(conn))
    body = (await api.get(f"/api/admin/shifts/{site['shifts'][0]}/summary", headers=_headers(site, "admin"))).json()
    assert body["source"] == "cloud" and body["incidents_total"] == 1
    assert body["time"]["working_minutes"] == 90.0
    assert body["tasks_total"] == 1
