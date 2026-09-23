"""Operator API integration tests against PostgreSQL (audit before phase 7).

Covers ownership checks, shift-scoped incidents, the acknowledgement
lifecycle through the API, manual reports, machine status, coaching, the
WebSocket access rule and snapshot, and incident broadcasts on close.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from app.api.ws.operator_ws import _may_watch, build_snapshot
from app.core.clock import set_sim_time
from app.db.models.edge.assignment import EdgeShift, EdgeTask
from app.db.models.edge.behavior import BehaviorEvent
from app.db.models.edge.incident import Incident
from app.edge.telemetry import state
from tests.integration.conftest import T0, _pipeline, _Recorder, db_session, make_tick


async def _add_incident(conn, world, **overrides) -> str:
    values = {
        "incident_id": str(uuid.uuid4()),
        "shift_id": world["shift_id"],
        "machine_id": world["machine_id"],
        "operator_id": world["operator_id"],
        "event_start": T0,
        "event_end": None,
        "incident_type": "proximity",
        "peak_severity": "critical",
        "status": "open",
        "source": "engine",
    }
    values.update(overrides)
    async with db_session(conn) as session:
        session.add(Incident(**values))
        await session.commit()
    return values["incident_id"]


async def _other_shift_task(conn, world) -> str:
    """A task on a different shift of the same machine, owned by nobody here."""
    async with db_session(conn) as session:
        other = EdgeShift(
            shift_id=str(uuid.uuid4()),
            machine_id=world["machine_id"],
            operator_id=world["operator_id"],
            date=T0 - timedelta(days=1),
            scheduled_start=T0 - timedelta(days=1),
            scheduled_end=T0 - timedelta(days=1) + timedelta(hours=8),
            weather_forecast="clear",
            weather_actual="clear",
        )
        session.add(other)
        await session.flush()
        task = EdgeTask(
            task_id=str(uuid.uuid4()),
            shift_id=other.shift_id,
            task_type="excavation",
            operator_skill_at_assignment="intermediate",
            target_quantity=10.0,
            quantity_unit="m3",
            material_type="clay",
            status="assigned",
            scheduled_start=other.scheduled_start,
        )
        session.add(task)
        await session.commit()
        return task.task_id


# ---------------------------------------------------------------------------
# Shift, status, tasks
# ---------------------------------------------------------------------------


async def test_current_shift_includes_operator_and_machine_details(client, world):
    body = (await client.get("/api/operator/shift/current")).json()
    assert body["operator_name"] == "Test Operator"
    assert body["machine_id"] == world["machine_id"]
    assert body["tilt_limit_degrees"] == 30.0
    assert body["rated_max_rpm"] == 2000.0


async def test_machine_status_is_unknown_until_live_data(client, world):
    before = (await client.get("/api/operator/status")).json()
    assert before["live"] is False and before["parked"] is False and before["engine_rpm"] is None

    state.record_tick(make_tick(world, 0))
    after = (await client.get("/api/operator/status")).json()
    assert after["live"] is True and after["parked"] is True and after["engine_rpm"] == 1400.0


async def test_tasks_only_from_current_shift(client, conn, world):
    other_task = await _other_shift_task(conn, world)
    ids = [t["task_id"] for t in (await client.get("/api/operator/tasks")).json()]
    assert ids == [world["task_id"]]
    assert other_task not in ids


async def test_action_on_a_task_outside_the_shift_is_forbidden(client, conn, world):
    other_task = await _other_shift_task(conn, world)
    response = await client.post(f"/api/operator/tasks/{other_task}/start")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_invalid_transition_is_rejected(client, world):
    response = await client.post(f"/api/operator/tasks/{world['task_id']}/resume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


# ---------------------------------------------------------------------------
# Incidents and acknowledgement
# ---------------------------------------------------------------------------


async def test_incident_list_is_shift_scoped_and_keeps_resolved(client, conn, world):
    open_id = await _add_incident(conn, world)
    resolved_id = await _add_incident(conn, world, status="resolved", event_end=T0, peak_severity="warning")
    ids = {i["incident_id"] for i in (await client.get("/api/operator/incidents")).json()}
    assert {open_id, resolved_id} <= ids


async def test_acknowledge_rejected_without_live_state(client, conn, world):
    incident_id = await _add_incident(conn, world)
    response = await client.post(f"/api/operator/incidents/{incident_id}/acknowledge")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACK_REQUIRES_STATIONARY"


async def test_acknowledge_rejected_while_moving(client, conn, world):
    incident_id = await _add_incident(conn, world)
    state.record_tick(make_tick(world, 0, machine_speed=4.0, park_brake=False, gear_state="forward"))
    response = await client.post(f"/api/operator/incidents/{incident_id}/acknowledge")
    assert response.status_code == 409


async def test_acknowledging_an_active_critical_keeps_it_open(client, conn, world):
    incident_id = await _add_incident(conn, world)
    state.record_tick(make_tick(world, 0))
    body = (await client.post(f"/api/operator/incidents/{incident_id}/acknowledge")).json()
    assert body["status"] == "acknowledged"
    assert body["acknowledged_at"] is not None


async def test_acknowledging_a_cleared_critical_resolves_it(client, conn, world):
    incident_id = await _add_incident(conn, world, event_end=T0 + timedelta(seconds=40))
    state.record_tick(make_tick(world, 0))
    body = (await client.post(f"/api/operator/incidents/{incident_id}/acknowledge")).json()
    assert body["status"] == "resolved"


async def test_cannot_acknowledge_another_operators_incident(client, conn, world):
    incident_id = await _add_incident(conn, world, operator_id=str(uuid.uuid4()))
    state.record_tick(make_tick(world, 0))
    response = await client.post(f"/api/operator/incidents/{incident_id}/acknowledge")
    assert response.status_code == 403


async def test_manual_report_is_recorded_and_resolves_on_acknowledge(client, world):
    created = await client.post("/api/operator/incidents", json={"description": "Near miss at the loading point"})
    assert created.status_code == 201
    report = created.json()
    assert report["incident_type"] == "manual_report"
    assert report["source"] == "operator"
    assert report["event_end"] is not None

    state.record_tick(make_tick(world, 0))
    acked = (await client.post(f"/api/operator/incidents/{report['incident_id']}/acknowledge")).json()
    assert acked["status"] == "resolved"


async def test_manual_report_needs_a_description(client, world):
    response = await client.post("/api/operator/incidents", json={"description": ""})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["field"] == "description"


async def test_auth_errors_use_the_standard_envelope(client, world):
    missing = await client.get("/api/operator/tasks", headers={"Authorization": ""})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"

    unknown = await client.get("/api/does-not-exist")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Coaching
# ---------------------------------------------------------------------------


async def test_coaching_lists_behavior_events_with_module(client, conn, world):
    async with db_session(conn) as session:
        session.add(BehaviorEvent(
            event_id=str(uuid.uuid4()),
            machine_id=world["machine_id"],
            operator_id=world["operator_id"],
            shift_id=world["shift_id"],
            event_type="excessive_idling",
            start=T0,
            end=T0 + timedelta(minutes=7),
            magnitude=7.0,
        ))
        await session.commit()

    notes = (await client.get("/api/operator/coaching")).json()
    assert len(notes) == 1
    assert "7 min" in notes[0]["message"]
    assert notes[0]["module_id"] == "fuel-efficient-operation"


# ---------------------------------------------------------------------------
# WebSocket access and snapshot
# ---------------------------------------------------------------------------


async def test_operator_may_watch_only_their_machine(conn, world):
    async with db_session(conn) as session:
        operator = {"role": "operator", "operator_id": world["operator_id"]}
        assert await _may_watch(session, operator, world["machine_id"])
        assert not await _may_watch(session, operator, str(uuid.uuid4()))
        assert not await _may_watch(session, {"role": "operator"}, world["machine_id"])
        assert await _may_watch(session, {"role": "admin"}, str(uuid.uuid4()))


async def test_snapshot_contains_everything_the_operator_view_needs(conn, world):
    await _add_incident(conn, world)
    async with db_session(conn) as session:
        snapshot = await build_snapshot(session, world["machine_id"])
    assert snapshot["shift"]["shift_id"] == world["shift_id"]
    assert [t["task_id"] for t in snapshot["tasks"]] == [world["task_id"]]
    assert len(snapshot["incidents"]) == 1
    assert snapshot["coaching"] == []
    assert snapshot["status"]["live"] is False


async def test_incident_broadcasts_carry_the_incident_id_on_close(conn, world):
    recorder = _Recorder()
    pipeline = _pipeline(conn, recorder)
    for second in range(20):
        await pipeline.process(make_tick(world, second, proximity_distance=8.0))
    for second in range(20, 60):
        await pipeline.process(make_tick(world, second))

    events = recorder.of_type("incident")
    assert [e["event"] for e in events] == ["opened", "closed"]
    assert events[0]["incident_id"] == events[1]["incident_id"] is not None


async def test_pause_time_is_stored_on_the_task(client, conn, world):
    """The pause start lives in the database, so a backend restart mid-pause keeps it (I-19)."""
    set_sim_time(T0 + timedelta(hours=2))
    assert (await client.post(f"/api/operator/tasks/{world['task_id']}/pause")).status_code == 200
    async with db_session(conn) as session:
        paused = await session.get(EdgeTask, world["task_id"])
        assert paused.hold_started_at == T0 + timedelta(hours=2)

    set_sim_time(T0 + timedelta(hours=2, minutes=12))
    assert (await client.post(f"/api/operator/tasks/{world['task_id']}/resume")).status_code == 200
    async with db_session(conn) as session:
        resumed = await session.get(EdgeTask, world["task_id"])
        assert resumed.paused_minutes == 12.0
        assert resumed.hold_started_at is None
