"""Phase 0 exit-criteria tests.

Covers:
- App factory creates a FastAPI instance without errors.
- GET /api/system/health returns 200 with status=ok.
- Thresholds load and validate correctly.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "sim_time" in data
    assert "cloud_reachable" in data


@pytest.mark.asyncio
async def test_thresholds_load() -> None:
    thresholds = get_thresholds()
    safety = thresholds.safety
    assert safety.seatbelt.warning_grace_seconds == 10
    assert safety.seatbelt.critical_grace_seconds == 30
    assert safety.proximity.warning_trigger_meters == 10.0
    assert safety.repeat_escalation.count_threshold == 3


@pytest.mark.asyncio
async def test_thresholds_behavior() -> None:
    t = get_thresholds()
    b = t.behavior
    assert b.excessive_idling_seconds == 300
    assert b.high_rpm_fraction == 0.85
    assert b.travel_speed_threshold_kmh.excavator == 3.0


@pytest.mark.asyncio
async def test_thresholds_eta() -> None:
    t = get_thresholds()
    e = t.eta
    assert not hasattr(e, "planning_buffer_minutes")  # single source: settings
    assert get_settings().eta_buffer_minutes == 15
    assert e.model.n_estimators == 150
    assert e.model.random_state == 42
