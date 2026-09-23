"""Phase 6 integration tests against PostgreSQL.

Each test runs inside one outer transaction that is rolled back, so the
database is left unchanged. Skipped when the database is not reachable.

Covers the phase 6 exit criteria and deliverable:
- duplicate recommendations for one module in one shift are impossible
- training content is blocked while the machine is moving
- a proximity scenario and an idling scenario each produce the right
  recommendation through the live edge pipeline
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.edge.assignment import EdgeTask
from app.db.models.edge.behavior import BehaviorEvent, Recommendation
from app.db.models.edge.incident import Incident
from app.edge.telemetry import state
from app.edge.training.recommendations import recommend_for_source
from app.main import app
from tests.integration.conftest import (
    T0,
    _pipeline,
    _recommended_modules,
    _Recorder,
    db_session,
    make_tick,
)

# ---------------------------------------------------------------------------
# Recommendation dedup (exit criterion)
# ---------------------------------------------------------------------------


async def test_same_module_is_recommended_once_per_shift(conn, world):
    async with db_session(conn) as session:
        outcomes = []
        # Three sources that all map to the seatbelt module.
        for source_type, type_value in [
            ("incident", "seatbelt"),
            ("incident", "seatbelt"),
            ("behavior", "repeated_seatbelt_violation"),
        ]:
            outcomes.append(await recommend_for_source(
                session,
                operator_id=world["operator_id"],
                shift_id=world["shift_id"],
                source_type=source_type,
                type_value=type_value,
                source_id=str(uuid.uuid4()),
                created_at=T0,
            ))
        await session.commit()

    assert [o.created for o in outcomes] == [True, False, False]
    assert await _recommended_modules(conn, world) == ["seatbelt-seat-safety"]


async def test_database_rejects_a_duplicate_recommendation_row(conn, world):
    row = {
        "operator_id": world["operator_id"],
        "shift_id": world["shift_id"],
        "module_id": "load-limits",
        "source_type": "incident",
        "created_at": T0,
    }
    async with db_session(conn) as session:
        session.add(Recommendation(recommendation_id=str(uuid.uuid4()), source_id="a", **row))
        await session.commit()
        session.add(Recommendation(recommendation_id=str(uuid.uuid4()), source_id="b", **row))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_next_shift_gets_a_fresh_recommendation(conn, world):
    async with db_session(conn) as session:
        for shift_id in (world["shift_id"], str(uuid.uuid4())):
            outcome = await recommend_for_source(
                session,
                operator_id=world["operator_id"],
                shift_id=shift_id,
                source_type="incident",
                type_value="proximity",
                source_id=str(uuid.uuid4()),
                created_at=T0,
            )
            assert outcome.created


async def test_unmapped_type_gives_no_recommendation(conn, world):
    async with db_session(conn) as session:
        outcome = await recommend_for_source(
            session,
            operator_id=world["operator_id"],
            shift_id=world["shift_id"],
            source_type="incident",
            type_value="manual_report",
            source_id=str(uuid.uuid4()),
            created_at=T0,
        )
    assert outcome is None


# ---------------------------------------------------------------------------
# Live pipeline: scenarios produce the right recommendation (deliverable)
# ---------------------------------------------------------------------------


async def test_proximity_scenario_recommends_working_near_people(conn, world):
    recorder = _Recorder()
    pipeline = _pipeline(conn, recorder)

    second = 0
    # Two separate breaches, each followed by a clear period past the cooldown.
    for _ in range(2):
        for _ in range(40):
            await pipeline.process(make_tick(world, second, proximity_distance=4.0))
            second += 1
        for _ in range(60):
            await pipeline.process(make_tick(world, second))
            second += 1

    async with db_session(conn) as session:
        incidents = (await session.execute(
            select(func.count()).select_from(Incident)
            .where(Incident.shift_id == world["shift_id"])
            .where(Incident.incident_type == "proximity")
        )).scalar_one()
    assert incidents == 2
    assert await _recommended_modules(conn, world) == ["working-near-people"]
    # Pushed to the operator once, not once per incident.
    assert [r["module_id"] for r in recorder.of_type("recommendation")] == ["working-near-people"]


async def test_idling_scenario_recommends_fuel_efficient_operation(conn, world):
    recorder = _Recorder()
    pipeline = _pipeline(conn, recorder)

    for second in range(310):
        await pipeline.process(make_tick(world, second, hydraulic_active=False, payload_pct=0.0))

    async with db_session(conn) as session:
        events = (await session.execute(
            select(BehaviorEvent.event_type).where(BehaviorEvent.shift_id == world["shift_id"])
        )).scalars().all()
    assert events == ["excessive_idling"]
    assert await _recommended_modules(conn, world) == ["fuel-efficient-operation"]

    coaching = recorder.of_type("coaching")
    assert len(coaching) == 1
    assert coaching[0]["recommendation"]["module_id"] == "fuel-efficient-operation"


async def test_cycle_ticks_add_task_progress(conn, world):
    recorder = _Recorder()
    pipeline = _pipeline(conn, recorder)

    for second in range(3):
        await pipeline.process(make_tick(world, second, load_cycles=1, cycle_payload_pct=100.0))

    async with db_session(conn) as session:
        task = await session.get(EdgeTask, world["task_id"])
    # 3 cycles x 100% fill x 1.2 m3 bucket
    assert task.completed_quantity == pytest.approx(3.6)
    assert recorder.of_type("progress")[-1]["target_reached"] is False


# ---------------------------------------------------------------------------
# API: parked-only training content (exit criterion)
# ---------------------------------------------------------------------------


_MODULE = "/api/training/modules/working-near-people"


async def test_module_list_is_available_while_not_parked(client, world):
    response = await client.get("/api/training/modules")
    assert response.status_code == 200
    body = response.json()
    assert body["parked"] is False
    assert len(body["modules"]) == 5
    assert all(m["status"] == "not_started" for m in body["modules"])


async def test_content_blocked_with_no_telemetry(client, world):
    response = await client.get(_MODULE)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MACHINE_NOT_PARKED"


async def test_content_and_quiz_blocked_while_moving(client, world):
    state.record_tick(make_tick(world, 0, machine_speed=5.0, park_brake=False, gear_state="forward"))

    content = await client.get(_MODULE)
    quiz = await client.post(f"{_MODULE}/quiz", json={"answers": [1, 1, 1, 2, 1]})
    assert content.status_code == 409
    assert quiz.status_code == 409
    assert quiz.json()["error"]["code"] == "MACHINE_NOT_PARKED"


async def test_parked_operator_reads_module_and_passes_quiz(client, world):
    state.record_tick(make_tick(world, 0))

    content = await client.get(_MODULE)
    assert content.status_code == 200
    body = content.json()
    assert body["body_markdown"].startswith("# Working near people")
    assert "answer" not in body["quiz"][0]

    result = await client.post(f"{_MODULE}/quiz", json={"answers": [1, 2, 1, 2, 1]})
    assert result.status_code == 200
    assert result.json()["score"] == 100.0
    assert result.json()["status"] == "passed"

    listing = (await client.get("/api/training/modules")).json()
    status = {m["module_id"]: m["status"] for m in listing["modules"]}
    assert listing["parked"] is True
    assert status["working-near-people"] == "passed"


async def test_recommendations_endpoint_lists_current_shift(client, conn, world):
    async with db_session(conn) as session:
        await recommend_for_source(
            session,
            operator_id=world["operator_id"],
            shift_id=world["shift_id"],
            source_type="behavior",
            type_value="excessive_idling",
            source_id=str(uuid.uuid4()),
            created_at=T0,
        )
        await session.commit()

    response = await client.get("/api/operator/recommendations")
    assert response.status_code == 200
    assert [r["module_id"] for r in response.json()] == ["fuel-efficient-operation"]


async def test_emergency_guidance_needs_no_login(conn):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/emergency")
    assert response.status_code == 200
    assert len(response.json()["items"]) >= 5


async def test_manual_search(client, world):
    response = await client.get("/api/assistant/search", params={"q": "check tyre pressure"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["section_title"] == "Tyre pressure"


async def test_manual_search_rejects_blank_query(client, world):
    response = await client.get("/api/assistant/search", params={"q": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
