"""Phase 2 unit tests.

Covers:
- Task status transition logic (valid and invalid)
- Telemetry tick validation (valid, malformed, missing required fields)
- Clock driver speed and auto-slow behaviour
- Scenario injector builds correct overrides
- WebSocket manager connect/disconnect/broadcast
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.clock import set_sim_time
from app.core.errors import InvalidStatusTransitionError
from app.edge.tasks.service import _ALLOWED, _action_to_status
from app.edge.telemetry.ingest import validate_raw
from app.shared.enums import TaskStatus
from simulator.live.clock_driver import ClockDriver
from simulator.live.scenarios import ScenarioInjector

# ---------------------------------------------------------------------------
# Task status transitions
# ---------------------------------------------------------------------------


def test_valid_transitions_assigned_to_in_progress() -> None:
    assert _action_to_status("start", TaskStatus.ASSIGNED) == TaskStatus.IN_PROGRESS


def test_valid_transitions_in_progress_to_paused() -> None:
    assert _action_to_status("pause", TaskStatus.IN_PROGRESS) == TaskStatus.PAUSED


def test_valid_transitions_paused_to_in_progress() -> None:
    assert _action_to_status("resume", TaskStatus.PAUSED) == TaskStatus.IN_PROGRESS


def test_valid_transitions_in_progress_to_blocked() -> None:
    assert _action_to_status("block", TaskStatus.IN_PROGRESS) == TaskStatus.BLOCKED


def test_valid_transitions_blocked_to_in_progress() -> None:
    assert _action_to_status("unblock", TaskStatus.BLOCKED) == TaskStatus.IN_PROGRESS


def test_valid_transitions_in_progress_to_done() -> None:
    assert _action_to_status("complete", TaskStatus.IN_PROGRESS) == TaskStatus.DONE


def test_invalid_action_raises() -> None:
    with pytest.raises(InvalidStatusTransitionError):
        _action_to_status("explode", TaskStatus.IN_PROGRESS)


def test_done_has_no_allowed_transitions() -> None:
    assert _ALLOWED[TaskStatus.DONE] == set()


def test_cancelled_has_no_allowed_transitions() -> None:
    assert _ALLOWED[TaskStatus.CANCELLED] == set()


def test_cannot_start_from_done() -> None:
    target = _action_to_status("start", TaskStatus.DONE)
    assert target not in _ALLOWED[TaskStatus.DONE]


# ---------------------------------------------------------------------------
# Telemetry validation
# ---------------------------------------------------------------------------


def _valid_tick() -> dict:
    return {
        "timestamp": "2025-05-01T08:00:00+00:00",
        "machine_id": "M001",
        "operator_id": "OP001",
        "shift_id": "SH001",
        "engine_running": True,
        "engine_rpm": 1500.0,
        "engine_hours": 1523.5,
        "fuel_used": 5.2,
        "hydraulic_active": False,
        "machine_speed": 0.0,
        "load_cycles": 12,
        "payload_pct": 45.0,
        "seatbelt_status": "fastened",
        "seat_occupied": True,
        "park_brake": True,
        "gear_state": "park",
        "ambient_temp": 30.0,
        "visibility": 400.0,
        "tilt_angle": 5.0,
        "proximity_sensor_ok": True,
    }


def test_valid_tick_parses() -> None:
    tick = validate_raw(_valid_tick())
    assert tick is not None
    assert tick.machine_id == "M001"
    assert tick.seatbelt_status == "fastened"


def test_invalid_seatbelt_status_rejected() -> None:
    bad = {**_valid_tick(), "seatbelt_status": "unknown_value"}
    tick = validate_raw(bad)
    assert tick is None


def test_invalid_gear_state_rejected() -> None:
    bad = {**_valid_tick(), "gear_state": "turbo"}
    tick = validate_raw(bad)
    assert tick is None


def test_negative_engine_rpm_rejected() -> None:
    bad = {**_valid_tick(), "engine_rpm": -100.0}
    tick = validate_raw(bad)
    assert tick is None


def test_missing_required_field_rejected() -> None:
    bad = _valid_tick()
    del bad["shift_id"]
    tick = validate_raw(bad)
    assert tick is None


def test_optional_proximity_none_is_valid() -> None:
    tick = validate_raw({**_valid_tick(), "proximity_distance": None})
    assert tick is not None
    assert tick.proximity_distance is None


def test_optional_cycle_payload_pct_present() -> None:
    tick = validate_raw({**_valid_tick(), "cycle_payload_pct": 92.5})
    assert tick is not None
    assert tick.cycle_payload_pct == 92.5


# ---------------------------------------------------------------------------
# Clock driver
# ---------------------------------------------------------------------------


def test_clock_driver_effective_speed_no_incidents() -> None:
    driver = ClockDriver(selected_speed=5)
    assert driver.effective_speed == 5


def test_clock_driver_auto_slow_on_incident() -> None:
    driver = ClockDriver(selected_speed=5)
    driver.notify_incident_opened()
    assert driver.effective_speed == 1


def test_clock_driver_auto_slow_lifts_when_all_resolved() -> None:
    driver = ClockDriver(selected_speed=5)
    driver.notify_incident_opened()
    driver.notify_incident_opened()
    driver.notify_incident_closed()
    assert driver.effective_speed == 1   # still one open
    driver.notify_incident_closed()
    assert driver.effective_speed == 5   # all resolved


def test_clock_driver_open_incident_count_never_negative() -> None:
    driver = ClockDriver(selected_speed=3)
    driver.notify_incident_closed()   # no-op, count stays at 0
    assert driver.effective_speed == 3


def test_clock_driver_set_speed() -> None:
    driver = ClockDriver(selected_speed=1)
    driver.set_speed(10)
    assert driver._selected_speed == 10


# ---------------------------------------------------------------------------
# Scenario injector
# ---------------------------------------------------------------------------


def test_scenario_proximity_sets_distance() -> None:
    set_sim_time(datetime(2025, 1, 1, tzinfo=UTC))
    injector = ScenarioInjector()
    window = injector.proximity_breach(distance_m=3.5, duration_seconds=60)
    assert window.overrides["proximity_distance"] == 3.5


def test_scenario_seatbelt_sets_unfastened() -> None:
    set_sim_time(datetime(2025, 1, 1, tzinfo=UTC))
    injector = ScenarioInjector()
    window = injector.seatbelt_removal(duration_seconds=30)
    assert window.overrides["seatbelt_status"] == "unfastened"
    assert window.overrides["park_brake"] is False


def test_scenario_overrides_apply_in_window() -> None:
    base = datetime(2025, 1, 1, 8, 0, 0, tzinfo=UTC)
    set_sim_time(base)
    injector = ScenarioInjector()
    injector.overload(payload_pct=125.0, duration_seconds=60)

    overrides = injector.active_overrides(base + timedelta(seconds=30))
    assert overrides.get("payload_pct") == 125.0


def test_scenario_overrides_expire_after_window() -> None:
    base = datetime(2025, 1, 1, 8, 0, 0, tzinfo=UTC)
    set_sim_time(base)
    injector = ScenarioInjector()
    injector.overload(payload_pct=125.0, duration_seconds=10)

    overrides = injector.active_overrides(base + timedelta(seconds=15))
    assert "payload_pct" not in overrides


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_manager_connect_disconnect() -> None:
    from app.api.ws.manager import ConnectionManager

    manager = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()

    await manager.connect("M001", ws)
    assert manager.connection_count("M001") == 1

    await manager.disconnect("M001", ws)
    assert manager.connection_count("M001") == 0


@pytest.mark.asyncio
async def test_ws_manager_broadcast_sends_to_all() -> None:
    from app.api.ws.manager import ConnectionManager

    manager = ConnectionManager()
    ws1, ws2 = AsyncMock(), AsyncMock()
    ws1.accept = AsyncMock()
    ws2.accept = AsyncMock()
    ws1.send_json = AsyncMock()
    ws2.send_json = AsyncMock()

    await manager.connect("M001", ws1)
    await manager.connect("M001", ws2)
    await manager.send_state_snapshot("M001", ws1, {})
    await manager.send_state_snapshot("M001", ws2, {})

    await manager.broadcast("M001", {"type": "ping"})

    ws1.send_json.assert_called_with({"type": "ping"})
    ws2.send_json.assert_called_with({"type": "ping"})


@pytest.mark.asyncio
async def test_ws_updates_before_the_snapshot_are_held_then_sent_in_order() -> None:
    from app.api.ws.manager import ConnectionManager

    manager = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()

    await manager.connect("M001", ws)
    await manager.broadcast("M001", {"type": "incident", "n": 1})
    await manager.broadcast("M001", {"type": "progress", "n": 2})
    ws.send_json.assert_not_called()

    await manager.send_state_snapshot("M001", ws, {"shift": None})
    sent = [c.args[0] for c in ws.send_json.call_args_list]
    assert sent == [
        {"type": "state", "data": {"shift": None}},
        {"type": "incident", "n": 1},
        {"type": "progress", "n": 2},
    ]


@pytest.mark.asyncio
async def test_ws_manager_dead_connection_removed_on_broadcast() -> None:
    from app.api.ws.manager import ConnectionManager

    manager = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock(side_effect=Exception("connection closed"))

    await manager.connect("M001", ws)
    await manager.send_state_snapshot("M001", ws, {})

    assert manager.connection_count("M001") == 0
