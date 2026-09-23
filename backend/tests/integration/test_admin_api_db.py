"""Admin assignment API against the database (phase 7).

Each rule is exercised through the HTTP API with an allowed and a rejected
case; the pure rule logic is covered in tests/unit/assignment.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.cloud.publish import queue_shift
from app.core.connectivity import set_cloud_reachable
from app.core.security import create_access_token, hash_password
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.db.models.cloud.user import AuditLog, User
from app.db.session import get_db
from app.edge.shift import current_shift_for_operator
from app.main import app
from app.shared.eta_model import buffer_minutes
from app.sync.worker import deliver_all
from tests.integration.conftest import T0, db_session

H = timedelta(hours=1)
ADMIN_PASSWORD = "admin-test-pass"


def _machine(machine_type: str, status: str = "available") -> Machine:
    return Machine(
        machine_id=str(uuid.uuid4()),
        machine_model="TEST",
        machine_type=machine_type,
        machine_age=3,
        machine_status=status,
        service_interval_hours=500.0,
        last_service_engine_hours=100.0,
        bucket_capacity=1.5,
        tilt_limit_degrees=30.0,
        rated_max_rpm=1800.0,
    )


def _operator(session: AsyncSession, quals: dict[str, str]) -> str:
    op = Operator(operator_id=str(uuid.uuid4()), operator_name="Test Operator")
    session.add(op)
    for machine_type, skill in quals.items():
        session.add(OperatorQualification(operator_id=op.operator_id, machine_type=machine_type, skill_level=skill))
    return op.operator_id


@pytest.fixture
async def site(conn: AsyncConnection) -> dict:
    """Operators, machines, an admin user, and one shift with no tasks."""
    async with db_session(conn) as session:
        machines = {
            "ex": _machine("excavator"),
            "ex2": _machine("excavator"),
            "wl": _machine("wheel_loader"),
            "ex_maint": _machine("excavator", "maintenance"),
        }
        session.add_all(machines.values())
        await session.flush()
        ops = {
            "ex_mid": _operator(session, {"excavator": "intermediate"}),
            "ex_expert": _operator(session, {"excavator": "expert"}),
            "wl_only": _operator(session, {"wheel_loader": "expert"}),
        }
        admin = User(
            user_id=str(uuid.uuid4()),
            role="admin",
            username=f"admin-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        session.add(admin)
        await session.flush()
        shift = Shift(
            shift_id=str(uuid.uuid4()),
            machine_id=machines["ex"].machine_id,
            operator_id=ops["ex_mid"],
            date=T0,
            scheduled_start=T0,
            scheduled_end=T0 + 8 * H,
            weather_forecast="clear",
            weather_actual=None,
        )
        session.add(shift)
        await session.commit()

    return {
        "machines": {k: m.machine_id for k, m in machines.items()},
        "ops": ops,
        "admin_id": admin.user_id,
        "admin_username": admin.username,
        "shift_id": shift.shift_id,
    }


@pytest.fixture
async def admin(conn: AsyncConnection, site: dict) -> AsyncIterator[AsyncClient]:
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with db_session(conn) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    token = create_access_token(user_id=site["admin_id"], role="admin")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


async def _audit(conn: AsyncConnection, action: str, target_id: str | None = None) -> list[AuditLog]:
    async with db_session(conn) as session:
        query = select(AuditLog).where(AuditLog.action == action)
        if target_id is not None:
            query = query.where(AuditLog.target_id == target_id)
        return list((await session.execute(query)).scalars().all())


def _shift_body(site: dict, operator: str, machine: str, start_h: float = 24, hours: float = 8) -> dict:
    start = T0 + start_h * H
    return {
        "operator_id": site["ops"][operator],
        "machine_id": site["machines"][machine],
        "scheduled_start": start.isoformat(),
        "scheduled_end": (start + hours * H).isoformat(),
        "weather_forecast": "rain",
    }


def _task_body(shift_id: str, start=None, task_type: str = "excavation", unit: str = "m3", qty: float = 40.0) -> dict:  # noqa: ANN001 - datetime or None
    body = {
        "shift_id": shift_id,
        "task_type": task_type,
        "target_quantity": qty,
        "quantity_unit": unit,
        "material_type": "clay",
    }
    if start is not None:
        body["scheduled_start"] = start.isoformat()
    return body


def _code(response) -> str:  # noqa: ANN001 - httpx response
    return response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# Master data and access
# ---------------------------------------------------------------------------


async def test_lists_operators_with_qualifications_and_machines(admin: AsyncClient, site: dict) -> None:
    operators = (await admin.get("/api/admin/operators")).json()
    mine = next(o for o in operators if o["operator_id"] == site["ops"]["wl_only"])
    assert mine["qualifications"] == [{"machine_type": "wheel_loader", "skill_level": "expert"}]

    machines = {m["machine_id"]: m for m in (await admin.get("/api/admin/machines")).json()}
    assert machines[site["machines"]["ex_maint"]]["machine_status"] == "maintenance"


async def test_operator_role_cannot_assign(admin: AsyncClient, site: dict) -> None:
    token = create_access_token(user_id=str(uuid.uuid4()), role="operator", operator_id=site["ops"]["ex_mid"])
    response = await admin.post(
        "/api/admin/shifts",
        json=_shift_body(site, "ex_mid", "ex"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert _code(response) == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Shift creation
# ---------------------------------------------------------------------------


async def test_create_shift_allowed_and_audited(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex"))
    assert response.status_code == 201
    shift = response.json()
    assert shift["weather_forecast"] == "rain"
    assert shift["weather_actual"] is None

    entries = await _audit(conn, "shift_create", shift["shift_id"])
    assert [(e.user_id, e.tier) for e in entries] == [(site["admin_id"], "cloud")]


async def test_create_shift_rejects_unqualified_operator(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "wl_only", "ex"))
    assert response.status_code == 409
    assert _code(response) == "OPERATOR_NOT_QUALIFIED"


async def test_create_shift_rejects_machine_in_maintenance(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex_maint"))
    assert response.status_code == 409
    assert _code(response) == "MACHINE_IN_MAINTENANCE"


async def test_create_shift_rejects_operator_overlap(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex2", start_h=4))
    assert response.status_code == 409
    assert _code(response) == "SHIFT_OVERLAP"
    assert response.json()["error"]["details"]["conflict_on"] == "operator"


async def test_create_shift_rejects_machine_overlap(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_expert", "ex", start_h=-2))
    assert response.status_code == 409
    assert response.json()["error"]["details"]["conflict_on"] == "machine"


async def test_back_to_back_shift_allowed(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex", start_h=8))
    assert response.status_code == 201


async def test_create_shift_rejects_reversed_window(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex", hours=-1))
    assert response.status_code == 400
    assert _code(response) == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------


async def test_create_task_stores_eta_and_derived_end(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))
    assert response.status_code == 201
    body = response.json()
    task = body["task"]
    assert body["warnings"] == []
    assert task["status"] == "assigned"
    assert task["operator_skill_at_assignment"] == "intermediate"
    assert task["scheduled_start"].startswith(T0.isoformat()[:16])
    assert task["planning_eta"] == pytest.approx(task["raw_predicted_time"] + buffer_minutes(), abs=0.11)
    assert task["model_version"] and task["aggregates_version"]

    async with db_session(conn) as session:
        stored = await session.get(Task, task["task_id"])
        assert stored.scheduled_end == stored.scheduled_start + timedelta(minutes=stored.planning_eta)
    assert len(await _audit(conn, "task_create", task["task_id"])) == 1


async def test_second_task_defaults_to_after_the_first(admin: AsyncClient, site: dict) -> None:
    first = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    second = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    assert second["scheduled_start"] == first["scheduled_end"]


async def test_create_task_rejects_incompatible_machine(admin: AsyncClient, site: dict) -> None:
    shift = (await admin.post("/api/admin/shifts", json=_shift_body(site, "wl_only", "wl"))).json()
    response = await admin.post("/api/admin/tasks", json=_task_body(shift["shift_id"], task_type="trenching"))
    assert response.status_code == 409
    assert _code(response) == "TASK_MACHINE_INCOMPATIBLE"


async def test_create_task_rejects_machine_now_in_maintenance(
    admin: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    async with db_session(conn) as session:
        machine = await session.get(Machine, site["machines"]["ex"])
        machine.machine_status = "maintenance"
        await session.commit()
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))
    assert response.status_code == 409
    assert _code(response) == "MACHINE_IN_MAINTENANCE"


async def test_create_task_rejects_wrong_unit(admin: AsyncClient, site: dict) -> None:
    response = await admin.post(
        "/api/admin/tasks", json=_task_body(site["shift_id"], task_type="material_loading", unit="m3")
    )
    assert response.status_code == 400
    assert _code(response) == "VALIDATION_ERROR"


async def test_create_task_rejects_overlap(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    first = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=T0))).json()["task"]
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=T0 + timedelta(minutes=5)))
    assert response.status_code == 409
    assert _code(response) == "TASK_OVERLAP"
    assert response.json()["error"]["details"]["conflicting_task_id"] == first["task_id"]
    # The rejected task left nothing behind.
    async with db_session(conn) as session:
        count = len((await session.execute(select(Task).where(Task.shift_id == site["shift_id"]))).scalars().all())
    assert count == 1


async def test_cancelled_task_no_longer_blocks_its_window(admin: AsyncClient, site: dict) -> None:
    first = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=T0))).json()["task"]
    await admin.post(f"/api/admin/tasks/{first['task_id']}/cancel")
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=T0))
    assert response.status_code == 201


async def test_task_past_shift_end_saved_with_warning(admin: AsyncClient, site: dict) -> None:
    late = T0 + 8 * H - timedelta(minutes=10)
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=late))
    assert response.status_code == 201
    warnings = response.json()["warnings"]
    assert [w["code"] for w in warnings] == ["SHIFT_END_EXCEEDED"]
    assert warnings[0]["details"]["overrun_minutes"] > 0


async def test_task_starting_outside_shift_rejected(admin: AsyncClient, site: dict) -> None:
    response = await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"], start=T0 - H))
    assert response.status_code == 400
    assert _code(response) == "VALIDATION_ERROR"


async def test_create_task_on_unknown_shift_is_not_found(admin: AsyncClient) -> None:
    response = await admin.post("/api/admin/tasks", json=_task_body(str(uuid.uuid4())))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


async def test_cancel_before_start(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    response = await admin.post(f"/api/admin/tasks/{task['task_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert len(await _audit(conn, "task_cancel", task["task_id"])) == 1

    again = await admin.post(f"/api/admin/tasks/{task['task_id']}/cancel")
    assert again.status_code == 409
    assert _code(again) == "INVALID_STATUS_TRANSITION"


async def _start(conn: AsyncConnection, task_id: str) -> None:
    async with db_session(conn) as session:
        task = await session.get(Task, task_id)
        task.status = "in_progress"
        task.actual_start = T0
        await session.commit()


async def test_cancel_after_start_rejected(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    await _start(conn, task["task_id"])
    response = await admin.post(f"/api/admin/tasks/{task['task_id']}/cancel")
    assert response.status_code == 409
    assert _code(response) == "TASK_ALREADY_STARTED"


# ---------------------------------------------------------------------------
# Reassign
# ---------------------------------------------------------------------------


async def test_reassign_before_start_recomputes_assignment(
    admin: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    target = (await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_expert", "ex2", start_h=24))).json()

    response = await admin.post(
        f"/api/admin/tasks/{task['task_id']}/reassign", json={"target_shift_id": target["shift_id"]}
    )
    assert response.status_code == 200
    moved = response.json()["task"]
    assert moved["task_id"] == task["task_id"]
    assert moved["shift_id"] == target["shift_id"]
    assert moved["operator_skill_at_assignment"] == "expert"
    assert moved["scheduled_start"] == target["scheduled_start"]
    assert moved["reassigned_at"] is not None

    async with db_session(conn) as session:
        stored = await session.get(Task, task["task_id"])
        assert stored.planning_eta == pytest.approx(stored.raw_predicted_time + buffer_minutes(), abs=0.11)
        assert stored.scheduled_end == stored.scheduled_start + timedelta(minutes=stored.planning_eta)
    assert len(await _audit(conn, "task_reassign", task["task_id"])) == 1


async def test_reassign_after_start_rejected(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    target = (await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_expert", "ex2"))).json()
    await _start(conn, task["task_id"])
    response = await admin.post(
        f"/api/admin/tasks/{task['task_id']}/reassign", json={"target_shift_id": target["shift_id"]}
    )
    assert response.status_code == 409
    assert _code(response) == "TASK_ALREADY_STARTED"


async def test_reassign_to_incompatible_shift_leaves_task_unchanged(
    admin: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    target = (await admin.post("/api/admin/shifts", json=_shift_body(site, "wl_only", "wl"))).json()
    response = await admin.post(
        f"/api/admin/tasks/{task['task_id']}/reassign", json={"target_shift_id": target["shift_id"]}
    )
    assert response.status_code == 409
    assert _code(response) == "TASK_MACHINE_INCOMPATIBLE"

    async with db_session(conn) as session:
        stored = await session.get(Task, task["task_id"])
        assert stored.shift_id == site["shift_id"]
        assert stored.raw_predicted_time == task["raw_predicted_time"]
        assert stored.reassigned_at is None


async def test_reassign_to_same_shift_rejected(admin: AsyncClient, site: dict) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    response = await admin.post(
        f"/api/admin/tasks/{task['task_id']}/reassign", json={"target_shift_id": site["shift_id"]}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_shift_tasks_and_eta_breakdown(admin: AsyncClient, site: dict) -> None:
    task = (await admin.post("/api/admin/tasks", json=_task_body(site["shift_id"]))).json()["task"]
    tasks = (await admin.get(f"/api/admin/shifts/{site['shift_id']}/tasks")).json()
    assert [t["task_id"] for t in tasks] == [task["task_id"]]

    shifts = (await admin.get("/api/admin/shifts", params={"operator_id": site["ops"]["ex_mid"]})).json()
    assert [s["shift_id"] for s in shifts] == [site["shift_id"]]

    breakdown = (await admin.get(f"/api/admin/tasks/{task['task_id']}/eta")).json()
    assert breakdown["raw_predicted_time"] == task["raw_predicted_time"]


# ---------------------------------------------------------------------------
# Audit for login and connectivity toggle
# ---------------------------------------------------------------------------


async def test_login_is_audited(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    response = await admin.post(
        "/api/auth/login", json={"username": site["admin_username"], "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    entries = await _audit(conn, "login", site["admin_id"])
    assert [e.user_id for e in entries] == [site["admin_id"]]


async def test_connectivity_toggle_is_audited_as_system_action(admin: AsyncClient, conn: AsyncConnection) -> None:
    try:
        response = await admin.post("/api/system/connectivity", json={"cloud_reachable": False})
        assert response.status_code == 200
    finally:
        set_cloud_reachable(True)
    entries = await _audit(conn, "connectivity_toggle", "offline")
    assert entries and all(e.user_id is None for e in entries)


# ---------------------------------------------------------------------------
# Current shift follows sim time
# ---------------------------------------------------------------------------


async def test_current_shift_follows_sim_time(admin: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    later = (await admin.post("/api/admin/shifts", json=_shift_body(site, "ex_mid", "ex", start_h=24))).json()
    operator_id = site["ops"]["ex_mid"]
    # The site shift was written straight to the cloud; publish it, then sync both to the edge.
    async with db_session(conn) as session:
        queue_shift(session, await session.get(Shift, site["shift_id"]))
        await session.commit()
    await deliver_all(lambda: db_session(conn))
    async with db_session(conn) as session:
        during_first = await current_shift_for_operator(session, operator_id, now=T0 + 2 * H)
        between = await current_shift_for_operator(session, operator_id, now=T0 + 12 * H)
        after_all = await current_shift_for_operator(session, operator_id, now=T0 + 100 * H)
        before_all = await current_shift_for_operator(session, operator_id, now=T0 - 100 * H)

    assert during_first.shift_id == site["shift_id"]
    assert between.shift_id == later["shift_id"]
    assert after_all.shift_id == later["shift_id"]
    assert before_all.shift_id == site["shift_id"]
