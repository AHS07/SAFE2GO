"""Sync, connectivity toggle, and offline login against the database (phase 8).

Exit criteria covered here:
  - duplicate delivery of any message has no side effect
  - out-of-order arrival never overwrites newer data
  - reassignment (and cancellation) conflict after an edge start
  - safety alerts continue with the cloud toggled off; records flush on reconnect
  - offline login works for the current shift's operator and fails after expiry
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.cloud.assignment.service import (
    TaskDraft,
    cancel_task,
    create_shift,
    create_task,
    reassign_task,
)
from app.cloud.publish import queue_machines, queue_operator_roster
from app.core.clock import set_sim_time, sim_now
from app.core.connectivity import set_cloud_reachable
from app.core.security import create_access_token, hash_password
from app.db.models.cloud.analytics import SyncConflict
from app.db.models.cloud.edge_records import CloudIncident
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.sync import CloudOutbox
from app.db.models.cloud.task import Task
from app.db.models.cloud.user import AuditLog, User
from app.db.models.edge.assignment import EdgeShift, EdgeTask
from app.db.models.edge.sync import EdgeOutbox
from app.db.session import get_db
from app.edge.telemetry import state
from app.edge.telemetry.pipeline import EdgePipeline
from app.main import app
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import SyncMessageType
from app.sync.handlers import cloud_to_edge, edge_to_cloud
from app.sync.outbox import decode_payload
from app.sync.worker import CLOUD_TO_EDGE, EDGE_TO_CLOUD, SyncWorker, deliver_all, deliver_pending
from tests.integration.conftest import T0, db_session

H = timedelta(hours=1)
PIN = "4321"
PASSWORD = "sync-test-pass"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _machine() -> Machine:
    return Machine(
        machine_id=str(uuid.uuid4()),
        machine_model="EX-TEST",
        machine_type="excavator",
        machine_age=2,
        machine_status="available",
        service_interval_hours=500.0,
        last_service_engine_hours=100.0,
        bucket_capacity=1.2,
        tilt_limit_degrees=30.0,
        rated_max_rpm=2000.0,
    )


async def _operator_user(session: AsyncSession, skill: str) -> tuple[str, str, str]:
    op = Operator(operator_id=str(uuid.uuid4()), operator_name=f"Sync {skill}")
    session.add(op)
    await session.flush()
    session.add(OperatorQualification(operator_id=op.operator_id, machine_type="excavator", skill_level=skill))
    user = User(
        user_id=str(uuid.uuid4()),
        role="operator",
        linked_operator_id=op.operator_id,
        username=f"sync-{uuid.uuid4().hex[:8]}",
        password_hash=hash_password(PASSWORD),
        pin_hash=hash_password(PIN),
    )
    session.add(user)
    await session.flush()
    return op.operator_id, user.user_id, user.username


@pytest.fixture
async def site(conn: AsyncConnection) -> AsyncIterator[dict]:
    """Two operators on two machines, one task each, created and synced like the app does."""
    saved_clock = sim_now()
    set_sim_time(T0 + H)
    set_cloud_reachable(True)
    async with db_session(conn) as session:
        machines = [_machine(), _machine()]
        session.add_all(machines)
        admin = User(user_id=str(uuid.uuid4()), role="admin", username=f"adm-{uuid.uuid4().hex[:8]}",
                     password_hash=hash_password(PASSWORD))
        session.add(admin)
        await session.flush()
        op_a, user_a, name_a = await _operator_user(session, "intermediate")
        op_b, user_b, name_b = await _operator_user(session, "expert")
        await queue_machines(session)
        await queue_operator_roster(session)

        shifts, tasks = [], []
        for operator_id, machine in ((op_a, machines[0]), (op_b, machines[1])):
            shift = await create_shift(
                session, operator_id=operator_id, machine_id=machine.machine_id,
                scheduled_start=T0, scheduled_end=T0 + 8 * H, weather_forecast="clear", actor_id=admin.user_id,
            )
            result = await create_task(session, TaskDraft(
                shift_id=shift.shift_id, task_type="excavation", target_quantity=30.0,
                quantity_unit="m3", material_type="clay",
            ), actor_id=admin.user_id)
            shifts.append(shift.shift_id)
            tasks.append(result.task.task_id)
        await session.commit()

    await deliver_all(lambda: db_session(conn))
    try:
        yield {
            "admin_id": admin.user_id,
            "ops": [op_a, op_b],
            "users": [user_a, user_b],
            "usernames": [name_a, name_b],
            "machines": [m.machine_id for m in machines],
            "shifts": shifts,
            "tasks": tasks,
        }
    finally:
        set_cloud_reachable(True)
        set_sim_time(saved_clock)
        state.clear()


@pytest.fixture
async def api(conn: AsyncConnection, site: dict) -> AsyncIterator[AsyncClient]:
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with db_session(conn) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


def _factory(conn: AsyncConnection):  # noqa: ANN202 - session factory
    return lambda: db_session(conn)


def _operator_headers(site: dict, index: int = 0) -> dict:
    token = create_access_token(user_id=site["users"][index], role="operator", operator_id=site["ops"][index])
    return {"Authorization": f"Bearer {token}"}


async def _edge_task(conn: AsyncConnection, task_id: str) -> EdgeTask | None:
    async with db_session(conn) as session:
        return await session.get(EdgeTask, task_id)


async def _cloud_task(conn: AsyncConnection, task_id: str) -> Task:
    async with db_session(conn) as session:
        return await session.get(Task, task_id)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def test_assignment_reaches_edge_through_sync(conn: AsyncConnection, site: dict) -> None:
    task = await _edge_task(conn, site["tasks"][0])
    assert task is not None and task.status == "assigned" and task.shift_id == site["shifts"][0]
    async with db_session(conn) as session:
        shift = await session.get(EdgeShift, site["shifts"][0])
        assert shift is not None and shift.machine_id == site["machines"][0]


async def test_duplicate_delivery_has_no_side_effect(conn: AsyncConnection, site: dict) -> None:
    async with db_session(conn) as session:
        before = (await session.execute(select(func.count()).select_from(EdgeTask))).scalar_one()
        # Pretend every delivered message was never acknowledged, so all are sent again.
        await session.execute(update(CloudOutbox).values(delivered_at=None))
        await session.commit()

    results = await deliver_all(_factory(conn))
    assert results["cloud_to_edge"].delivered == 0
    assert results["cloud_to_edge"].duplicates > 0
    async with db_session(conn) as session:
        after = (await session.execute(select(func.count()).select_from(EdgeTask))).scalar_one()
    assert after == before


async def _messages(conn: AsyncConnection, outbox: type, message_type: SyncMessageType) -> list:
    async with db_session(conn) as session:
        return list((await session.execute(
            select(outbox).where(outbox.message_type == message_type.value).order_by(outbox.seq)
        )).scalars().all())


@pytest.mark.parametrize("message_type", [
    SyncMessageType.MACHINE_MASTER,
    SyncMessageType.OPERATOR_ROSTER,
    SyncMessageType.SHIFT_RECORD,
    SyncMessageType.TASK_ASSIGNED,
    SyncMessageType.OFFLINE_CREDENTIAL,
])
async def test_cloud_handlers_are_idempotent_without_the_inbox(
    conn: AsyncConnection, site: dict, message_type: SyncMessageType
) -> None:
    """Applying the same message twice, bypassing the inbox check, leaves the same state."""
    message = (await _messages(conn, CloudOutbox, message_type))[0]
    handler = cloud_to_edge.HANDLERS[message_type.value]
    async with db_session(conn) as session:
        await handler(session, decode_payload(message.payload), message.seq)
        await handler(session, decode_payload(message.payload), message.seq)
        await session.commit()
    task = await _edge_task(conn, site["tasks"][0])
    assert task.status == "assigned"


async def test_older_task_message_never_overwrites_newer(conn: AsyncConnection, site: dict) -> None:
    async with db_session(conn) as session:
        target = await create_shift(
            session, operator_id=site["ops"][0], machine_id=site["machines"][0],
            scheduled_start=T0 + 24 * H, scheduled_end=T0 + 32 * H, weather_forecast="clear",
            actor_id=site["admin_id"],
        )
        await reassign_task(session, site["tasks"][0], target_shift_id=target.shift_id,
                            scheduled_start=None, actor_id=site["admin_id"])
        await session.commit()
    await deliver_all(_factory(conn))

    # Replay the original assignment (older sequence) after the reassignment.
    original = (await _messages(conn, CloudOutbox, SyncMessageType.TASK_ASSIGNED))[0]
    async with db_session(conn) as session:
        await cloud_to_edge.apply_task_assignment(session, decode_payload(original.payload), original.seq)
        await session.commit()
    task = await _edge_task(conn, site["tasks"][0])
    assert task.shift_id == target.shift_id


async def test_older_incident_state_never_overwrites_newer(conn: AsyncConnection, site: dict) -> None:
    incident_id = str(uuid.uuid4())
    base = {
        "incident_id": incident_id, "shift_id": site["shifts"][0], "machine_id": site["machines"][0],
        "operator_id": site["ops"][0], "task_id": None, "event_start": T0.isoformat(), "event_end": None,
        "incident_type": "proximity", "peak_severity": "warning", "escalation_reason": "threshold",
        "status": "open", "source": "engine", "description": None, "acknowledged_at": None,
        "acknowledged_by": None,
    }
    async with db_session(conn) as session:
        await edge_to_cloud.apply_incident(session, {**base, "peak_severity": "critical"}, 20)
        await edge_to_cloud.apply_incident(session, base, 10)
        await session.commit()
        incident = await session.get(CloudIncident, incident_id)
    assert incident.peak_severity == "critical"


# ---------------------------------------------------------------------------
# Connectivity toggle: queued while offline, flushed in order on reconnect
# ---------------------------------------------------------------------------


async def test_assignment_queued_while_offline_then_delivered(
    api: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    worker = SyncWorker(_factory(conn))
    admin_headers = {"Authorization": f"Bearer {create_access_token(user_id=site['admin_id'], role='admin')}"}
    set_cloud_reachable(False)
    response = await api.post("/api/admin/tasks", headers=admin_headers, json={
        "shift_id": site["shifts"][0], "task_type": "trenching", "target_quantity": 10,
        "quantity_unit": "m3", "material_type": "gravel",
    })
    assert response.status_code == 201
    new_task = response.json()["task"]
    assert new_task["delivery"] == "queued"

    await worker.run_once()
    assert await _edge_task(conn, new_task["task_id"]) is None
    status = (await api.get("/api/system/status")).json()
    assert status["cloud_outbox_pending"] >= 1

    set_cloud_reachable(True)
    await worker.run_once()
    assert (await _edge_task(conn, new_task["task_id"])) is not None
    tasks = (await api.get(f"/api/admin/shifts/{site['shifts'][0]}/tasks", headers=admin_headers)).json()
    assert {t["task_id"]: t["delivery"] for t in tasks}[new_task["task_id"]] == "delivered"


def _tick(site: dict, second: int, **overrides) -> TelemetryTick:
    raw = {
        "timestamp": T0 + H + timedelta(seconds=second), "machine_id": site["machines"][0],
        "operator_id": site["ops"][0], "task_id": None, "shift_id": site["shifts"][0],
        "engine_running": True, "engine_rpm": 1400.0, "engine_hours": 100.0, "fuel_used": 1.0,
        "hydraulic_active": True, "machine_speed": 0.0, "load_cycles": 0, "payload_pct": 50.0,
        "seatbelt_status": "fastened", "seat_occupied": True, "park_brake": True, "gear_state": "park",
        "ambient_temp": 25.0, "visibility": 400.0, "tilt_angle": 2.0,
    }
    raw.update(overrides)
    return TelemetryTick.model_validate(raw)


async def test_safety_keeps_working_offline_and_records_flush_on_reconnect(
    conn: AsyncConnection, site: dict
) -> None:
    async def _ignore(machine_id: str, message: dict) -> None:
        return None

    pipeline = EdgePipeline(_factory(conn), _ignore)
    worker = SyncWorker(_factory(conn))
    set_cloud_reachable(False)
    for second in range(6):
        await pipeline.process(_tick(site, second, proximity_distance=3.0))
    await worker.run_once()

    async with db_session(conn) as session:
        queued = (await session.execute(select(func.count()).select_from(EdgeOutbox).where(
            EdgeOutbox.message_type == SyncMessageType.INCIDENT.value, EdgeOutbox.delivered_at.is_(None),
        ))).scalar_one()
        cloud_copy = (await session.execute(select(CloudIncident).where(
            CloudIncident.shift_id == site["shifts"][0]))).scalars().all()
    assert queued >= 1, "the safety engine opened an incident while offline"
    assert cloud_copy == []

    set_cloud_reachable(True)
    await worker.run_once()
    async with db_session(conn) as session:
        synced = (await session.execute(select(CloudIncident).where(
            CloudIncident.shift_id == site["shifts"][0]))).scalars().all()
    assert [i.incident_type for i in synced] == ["proximity"]


async def test_failed_message_blocks_later_ones_and_records_error(conn: AsyncConnection, site: dict) -> None:
    async with db_session(conn) as session:
        session.add(CloudOutbox(
            message_id=str(uuid.uuid4()), idempotency_key=f"bad:{uuid.uuid4().hex}", message_type="not_a_type",
            payload="{}", created_at=sim_now(), attempts=0,
        ))
        await session.commit()
        await cancel_task(session, site["tasks"][1], actor_id=site["admin_id"])
        await session.commit()

    result = await deliver_pending(_factory(conn), CLOUD_TO_EDGE)
    assert result.failed_message_id is not None
    async with db_session(conn) as session:
        failed = await session.get(CloudOutbox, result.failed_message_id)
        assert failed.attempts == 1 and "No handler" in failed.last_error
        await session.delete(failed)
        await session.commit()
    assert (await _edge_task(conn, site["tasks"][1])).status == "assigned"

    await deliver_pending(_factory(conn), CLOUD_TO_EDGE)
    assert (await _edge_task(conn, site["tasks"][1])).status == "cancelled"


# ---------------------------------------------------------------------------
# Task status and conflicts
# ---------------------------------------------------------------------------


async def test_task_status_reaches_cloud(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    response = await api.post(f"/api/operator/tasks/{site['tasks'][0]}/start", headers=_operator_headers(site))
    assert response.status_code == 200
    await deliver_all(_factory(conn))
    task = await _cloud_task(conn, site["tasks"][0])
    assert task.status == "in_progress" and task.actual_start is not None

    async with db_session(conn) as session:
        entries = (await session.execute(select(AuditLog).where(
            AuditLog.action == "task_status_change", AuditLog.target_id == site["tasks"][0]))).scalars().all()
    assert [(e.tier, e.user_id) for e in entries] == [("edge", site["users"][0])]


async def test_reassignment_after_edge_start_is_rolled_back(
    api: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    task_id = site["tasks"][0]
    set_cloud_reachable(False)
    # Edge starts the task while offline.
    assert (await api.post(f"/api/operator/tasks/{task_id}/start", headers=_operator_headers(site))).status_code == 200
    # Cloud, not yet aware, moves the task to operator B's shift.
    set_sim_time(T0 + H + timedelta(minutes=5))
    async with db_session(conn) as session:
        await reassign_task(session, task_id, target_shift_id=site["shifts"][1],
                            scheduled_start=None, actor_id=site["admin_id"])
        await session.commit()

    set_cloud_reachable(True)
    await deliver_all(_factory(conn))

    edge = await _edge_task(conn, task_id)
    assert edge.shift_id == site["shifts"][0], "the edge ignores reassignment of a started task"
    cloud = await _cloud_task(conn, task_id)
    assert cloud.shift_id == site["shifts"][0] and cloud.status == "in_progress"
    assert cloud.reassigned_at is None
    async with db_session(conn) as session:
        conflicts = (await session.execute(select(SyncConflict).where(SyncConflict.task_id == task_id))).scalars().all()
    assert [c.conflict_type for c in conflicts] == ["reassign_after_start"]
    assert conflicts[0].edge_actual_start < conflicts[0].cloud_reassigned_at


async def test_cancel_after_edge_start_is_rolled_back(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    task_id = site["tasks"][0]
    set_cloud_reachable(False)
    await api.post(f"/api/operator/tasks/{task_id}/start", headers=_operator_headers(site))
    async with db_session(conn) as session:
        await cancel_task(session, task_id, actor_id=site["admin_id"])
        await session.commit()
    set_cloud_reachable(True)
    await deliver_all(_factory(conn))

    assert (await _edge_task(conn, task_id)).status == "in_progress"
    assert (await _cloud_task(conn, task_id)).status == "in_progress"
    admin_headers = {"Authorization": f"Bearer {create_access_token(user_id=site['admin_id'], role='admin')}"}
    conflicts = (await api.get("/api/admin/conflicts", headers=admin_headers)).json()
    mine = [c for c in conflicts if c["task_id"] == task_id]
    assert [c["conflict_type"] for c in mine] == ["cancel_after_start"]

    resolved = await api.post(f"/api/admin/conflicts/{mine[0]['conflict_id']}/resolve", headers=admin_headers)
    assert resolved.json()["resolved"] is True


async def test_reassignment_before_start_is_not_a_conflict(conn: AsyncConnection, site: dict) -> None:
    async with db_session(conn) as session:
        await reassign_task(session, site["tasks"][0], target_shift_id=site["shifts"][1],
                            scheduled_start=None, actor_id=site["admin_id"])
        await session.commit()
    await deliver_all(_factory(conn))
    assert (await _edge_task(conn, site["tasks"][0])).shift_id == site["shifts"][1]
    async with db_session(conn) as session:
        count = (await session.execute(select(func.count()).select_from(SyncConflict).where(
            SyncConflict.task_id == site["tasks"][0]))).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# Offline login
# ---------------------------------------------------------------------------


async def test_offline_login_for_current_shift_operator(api: AsyncClient, site: dict) -> None:
    set_cloud_reachable(False)
    response = await api.post("/api/auth/offline-login", json={"username": site["usernames"][0], "pin": PIN})
    assert response.status_code == 200
    body = response.json()
    assert body["offline"] is True and body["operator_id"] == site["ops"][0]

    tasks = await api.get("/api/operator/tasks", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert [t["task_id"] for t in tasks.json()] == [site["tasks"][0]]


async def test_offline_login_rejects_wrong_pin(api: AsyncClient, site: dict) -> None:
    response = await api.post("/api/auth/offline-login", json={"username": site["usernames"][0], "pin": "0000"})
    assert response.status_code == 401


async def test_offline_login_fails_after_expiry(api: AsyncClient, site: dict) -> None:
    set_sim_time(T0 + 8 * H + timedelta(minutes=61))
    response = await api.post("/api/auth/offline-login", json={"username": site["usernames"][0], "pin": PIN})
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


async def test_offline_login_works_within_grace_after_shift_end(api: AsyncClient, site: dict) -> None:
    set_sim_time(T0 + 8 * H + timedelta(minutes=59))
    response = await api.post("/api/auth/offline-login", json={"username": site["usernames"][0], "pin": PIN})
    assert response.status_code == 200


async def test_credential_is_bound_to_its_machine(conn: AsyncConnection, site: dict) -> None:
    from app.core.errors import UnauthorizedError
    from app.core.security import verify_offline_credential
    from app.db.models.edge.assignment import EdgeOfflineCredential

    async with db_session(conn) as session:
        credential = (await session.execute(select(EdgeOfflineCredential).where(
            EdgeOfflineCredential.shift_id == site["shifts"][0]))).scalar_one()
    with pytest.raises(UnauthorizedError):
        verify_offline_credential(credential.signature, site["machines"][1], PIN, sim_now())
    payload = verify_offline_credential(credential.signature, site["machines"][0], PIN, sim_now())
    assert "password" not in str(payload)


async def test_operator_password_login_needs_the_cloud(api: AsyncClient, site: dict) -> None:
    set_cloud_reachable(False)
    response = await api.post("/api/auth/login", json={"username": site["usernames"][0], "password": PASSWORD})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CLOUD_UNAVAILABLE"


async def test_offline_login_is_audited_through_sync(api: AsyncClient, conn: AsyncConnection, site: dict) -> None:
    await api.post("/api/auth/offline-login", json={"username": site["usernames"][0], "pin": PIN})
    await deliver_pending(_factory(conn), EDGE_TO_CLOUD)
    async with db_session(conn) as session:
        entries = (await session.execute(select(AuditLog).where(
            AuditLog.action == "offline_login", AuditLog.user_id == site["users"][0]))).scalars().all()
    assert [e.tier for e in entries] == ["edge"]


async def test_edge_handlers_are_idempotent_without_the_inbox(
    api: AsyncClient, conn: AsyncConnection, site: dict
) -> None:
    """Every edge message applied twice, bypassing the inbox check, leaves one record."""
    await api.post(f"/api/operator/tasks/{site['tasks'][0]}/start", headers=_operator_headers(site))
    await api.post("/api/operator/incidents", headers=_operator_headers(site), json={"description": "Loose gravel"})
    async with db_session(conn) as session:
        messages = (await session.execute(
            select(EdgeOutbox).where(EdgeOutbox.delivered_at.is_(None)).order_by(EdgeOutbox.seq)
        )).scalars().all()
        types = {m.message_type for m in messages}
        for message in messages:
            handler = edge_to_cloud.HANDLERS[message.message_type]
            await handler(session, decode_payload(message.payload), message.seq)
            await handler(session, decode_payload(message.payload), message.seq)
        await session.commit()
        audits = (await session.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.target_id == site["tasks"][0], AuditLog.tier == "edge"))).scalar_one()
        incidents = (await session.execute(select(func.count()).select_from(CloudIncident).where(
            CloudIncident.shift_id == site["shifts"][0]))).scalar_one()
    assert {"task_status", "incident", "audit_record"} <= types
    assert audits == 1 and incidents == 1
    assert (await _cloud_task(conn, site["tasks"][0])).status == "in_progress"
