"""Tests for the pre-phase-7 audit fixes.

Covers: acknowledgement rules and incident lifecycle, per-machine
scenarios, live stream motion (parked versus working), detection report
scoring, edge replay of batch history, and history task outcomes.
No database needed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import AckRequiresStationaryError, ForbiddenError
from app.db.models.edge.incident import Incident
from app.edge.behavior.coaching import coaching_message
from app.edge.safety.acknowledgement import acknowledge, status_after_hazard_clears
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import BehaviorEventType, IncidentStatus, Severity, TaskStatus
from evaluation.detection_report import Window, evaluate
from simulator.batch.edge_replay import ShiftIdleRecord, idle_ratio_events, replay_shift
from simulator.batch.generate import _history_status
from simulator.batch.telemetry import _Phase
from simulator.live import stream as stream_module
from simulator.live.scenarios import ScenarioInjector
from simulator.live.stream import LiveStream

T0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)


def _tick(**overrides) -> TelemetryTick:
    raw = {
        "timestamp": T0, "machine_id": "M-1", "operator_id": "OP-1", "task_id": None, "shift_id": "SH-1",
        "engine_running": True, "engine_rpm": 700.0, "engine_hours": 1.0, "fuel_used": 0.0,
        "hydraulic_active": False, "machine_speed": 0.0, "load_cycles": 0, "payload_pct": 0.0,
        "seatbelt_status": "fastened", "seat_occupied": True, "park_brake": True, "gear_state": "park",
        "ambient_temp": 25.0, "visibility": 400.0, "tilt_angle": 1.0,
        "proximity_sensor_ok": True,
    }
    raw.update(overrides)
    return TelemetryTick.model_validate(raw)


def _incident(**overrides) -> Incident:
    values = {
        "incident_id": "I-1", "shift_id": "SH-1", "machine_id": "M-1", "operator_id": "OP-1",
        "event_start": T0, "event_end": None, "incident_type": "proximity",
        "peak_severity": Severity.CRITICAL.value, "status": IncidentStatus.OPEN.value,
        "source": "engine", "acknowledged_at": None, "acknowledged_by": None,
    }
    values.update(overrides)
    return Incident(**values)


# ---------------------------------------------------------------------------
# Acknowledgement and incident lifecycle (architecture.md 4.2)
# ---------------------------------------------------------------------------


class TestAcknowledgement:
    def test_active_critical_becomes_acknowledged_not_resolved(self):
        incident = _incident()
        acknowledge(incident, _tick(), "OP-1", T0)
        assert incident.status == IncidentStatus.ACKNOWLEDGED.value
        assert incident.acknowledged_by == "OP-1"

    def test_cleared_critical_resolves_on_acknowledge(self):
        incident = _incident(event_end=T0 + timedelta(seconds=30))
        acknowledge(incident, _tick(), "OP-1", T0)
        assert incident.status == IncidentStatus.RESOLVED.value

    def test_rejected_while_moving(self):
        with pytest.raises(AckRequiresStationaryError):
            acknowledge(_incident(), _tick(machine_speed=3.0), "OP-1", T0)

    def test_rejected_when_machine_state_unknown(self):
        with pytest.raises(AckRequiresStationaryError) as exc:
            acknowledge(_incident(), None, "OP-1", T0)
        assert exc.value.details["reason"] == "no_telemetry"

    def test_rejected_for_another_operator(self):
        with pytest.raises(ForbiddenError):
            acknowledge(_incident(), _tick(), "OP-2", T0)

    def test_resolved_incident_is_left_alone(self):
        incident = _incident(status=IncidentStatus.RESOLVED.value)
        acknowledge(incident, None, "OP-1", T0)
        assert incident.status == IncidentStatus.RESOLVED.value
        assert incident.acknowledged_at is None

    @pytest.mark.parametrize(
        ("severity", "status", "expected"),
        [
            (Severity.WARNING.value, IncidentStatus.OPEN.value, IncidentStatus.RESOLVED.value),
            (Severity.CRITICAL.value, IncidentStatus.OPEN.value, IncidentStatus.OPEN.value),
            (Severity.CRITICAL.value, IncidentStatus.ACKNOWLEDGED.value, IncidentStatus.RESOLVED.value),
        ],
    )
    def test_status_when_hazard_clears(self, severity, status, expected):
        assert status_after_hazard_clears(severity, status) == expected


def test_coaching_message_describes_pattern_and_benefit():
    text = coaching_message(BehaviorEventType.EXCESSIVE_IDLING.value, 12.0)
    assert "12 min" in text and "fuel" in text


# ---------------------------------------------------------------------------
# Per-machine scenarios (I-7)
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_scenario_targets_one_machine(self, monkeypatch):
        monkeypatch.setattr("simulator.live.scenarios.sim_now", lambda: T0)
        injector = ScenarioInjector()
        injector.proximity_breach(machine_id="M-1")
        at = T0 + timedelta(seconds=5)
        assert injector.active_overrides(at, "M-1") == {"proximity_distance": 4.0}
        assert injector.active_overrides(at, "M-2") == {}

    def test_scenario_without_machine_applies_to_all(self, monkeypatch):
        monkeypatch.setattr("simulator.live.scenarios.sim_now", lambda: T0)
        injector = ScenarioInjector()
        injector.tilt()
        at = T0 + timedelta(seconds=5)
        assert injector.active_overrides(at, "M-1") == injector.active_overrides(at, "M-2") != {}

    def test_active_windows_and_clear(self, monkeypatch):
        monkeypatch.setattr("simulator.live.scenarios.sim_now", lambda: T0)
        injector = ScenarioInjector()
        injector.overload(machine_id="M-1")
        assert [w.name for w in injector.active_windows(T0 + timedelta(seconds=1))] == ["overload"]
        injector.clear()
        assert injector.active_windows(T0 + timedelta(seconds=1)) == []


# ---------------------------------------------------------------------------
# Live stream motion (I-8)
# ---------------------------------------------------------------------------


def _machine():
    return SimpleNamespace(
        machine_id="M-1", machine_type="excavator", machine_age=2, rated_max_rpm=2000.0,
        tilt_limit_degrees=30.0, last_service_engine_hours=100.0, engine_hours=420.0, bucket_capacity=1.2,
    )


def _shift():
    return SimpleNamespace(shift_id="SH-1", operator_id="OP-1", weather_actual="clear", weather_forecast="clear")


def _task():
    return SimpleNamespace(
        task_id="T-1", task_type="excavation", operator_skill_at_assignment="intermediate",
        material_type="clay", quantity_unit="m3",
    )


class TestStreamMotion:
    def test_no_task_means_parked(self):
        tick = LiveStream("M-1", seed=1)._build_tick(T0, _machine(), _shift(), None)
        assert tick["park_brake"] is True and tick["machine_speed"] == 0.0

    def test_working_releases_park_brake(self):
        tick = LiveStream("M-1", seed=1)._build_tick(T0, _machine(), _shift(), _task())
        assert tick["park_brake"] is False

    def test_repositions_after_a_set_number_of_cycles(self):
        stream = LiveStream("M-1", seed=1)
        machine, shift, task = _machine(), _shift(), _task()
        cycle_s = stream_module._cycle_seconds_for(machine, shift, task)
        seconds = cycle_s * stream_module._REPOSITION_EVERY_CYCLES + stream_module._REPOSITION_SECONDS
        ticks = [stream._build_tick(T0 + timedelta(seconds=i), machine, shift, task) for i in range(seconds)]

        travel = [t for t in ticks if t["machine_speed"] > 0]
        assert len(travel) == stream_module._REPOSITION_SECONDS
        assert all(not t["hydraulic_active"] and t["load_cycles"] == 0 for t in travel)
        assert sum(t["load_cycles"] for t in ticks) == stream_module._REPOSITION_EVERY_CYCLES


# ---------------------------------------------------------------------------
# Detection report scoring (I-4)
# ---------------------------------------------------------------------------


def _w(shift: str, kind: str, start_s: int, end_s: int) -> Window:
    return Window(shift, kind, T0 + timedelta(seconds=start_s), T0 + timedelta(seconds=end_s))


class TestDetectionScoring:
    def test_repeated_windows_count_as_one_anomaly(self):
        anomalies = [_w("S1", "repeated_overloading", s, s + 60) for s in (0, 600, 1200)]
        events = [_w("S1", "repeated_overloading", 1210, 1211)]
        result = evaluate(anomalies, events)
        assert result.total_detected == 1 and result.total_injected == 1

    def test_idling_inside_injected_idle_ratio_window_is_explained(self):
        anomalies = [_w("S1", "high_idle_ratio", 0, 1800)]
        events = [_w("S1", "excessive_idling", 100, 500), _w("S1", "high_idle_ratio", 0, 28800)]
        result = evaluate(anomalies, events)
        assert result.total_false_positives == 0
        assert result.explained["excessive_idling"] == 1

    def test_idling_on_legit_wait_shift_is_a_false_positive(self):
        anomalies = [_w("S2", "legit_wait", 0, 3600)]
        events = [_w("S2", "excessive_idling", 100, 500)]
        result = evaluate(anomalies, events)
        assert result.legit_wait_false_positives == 1
        assert result.total_false_positives == 1

    def test_missed_anomaly(self):
        result = evaluate([_w("S1", "high_rpm_travel", 0, 60)], [])
        assert result.missed["high_rpm_travel"] == 1


# ---------------------------------------------------------------------------
# Edge replay of batch history (I-4)
# ---------------------------------------------------------------------------


def _batch_tick(second: int, phase: str, task_id: str | None = "T-1", **overrides) -> dict:
    tick = _tick(task_id=task_id, timestamp=T0 + timedelta(seconds=second), **overrides).model_dump()
    tick["_phase"] = phase
    return tick


class TestEdgeReplay:
    def test_blocked_wait_is_not_excessive_idling(self):
        ticks = [_batch_tick(i, _Phase.BLOCKED) for i in range(900)]
        assert replay_shift(ticks, 2000.0, "excavator") == []

    def test_idle_while_working_is_excessive_idling(self):
        ticks = [_batch_tick(i, _Phase.IDLE) for i in range(400)]
        events = replay_shift(ticks, 2000.0, "excavator")
        assert [e.event_type for e in events] == [BehaviorEventType.EXCESSIVE_IDLING.value]

    def test_repeated_overload_incidents_raise_a_behavior_event(self):
        ticks = []
        second = 0
        for _ in range(3):
            ticks += [_batch_tick(second + i, _Phase.WORKING, payload_pct=112.0, hydraulic_active=True) for i in range(10)]
            second += 10
            ticks += [_batch_tick(second + i, _Phase.WORKING, payload_pct=50.0, hydraulic_active=True) for i in range(60)]
            second += 60
        events = replay_shift(ticks, 2000.0, "excavator")
        assert BehaviorEventType.REPEATED_OVERLOADING.value in [e.event_type for e in events]

    def test_idle_ratio_uses_baseline_window_only(self):
        history = [
            ShiftIdleRecord(f"H{i}", "OP-1", "excavator", T0, T0, 0.10 + 0.01 * (i % 3), True) for i in range(12)
        ]
        held_out = ShiftIdleRecord("X", "OP-1", "excavator", T0, T0 + timedelta(hours=8), 0.60, False)
        normal = ShiftIdleRecord("Y", "OP-1", "excavator", T0, T0 + timedelta(hours=8), 0.11, False)
        events = idle_ratio_events([*history, held_out, normal])
        assert "X" in events and "Y" not in events

    def test_idle_ratio_falls_back_to_fleet_baseline(self):
        fleet = [ShiftIdleRecord(f"F{i}", f"OP-{i}", "excavator", T0, T0, 0.10, True) for i in range(12)]
        newcomer = ShiftIdleRecord("N", "OP-new", "excavator", T0, T0, 0.50, False)
        assert "N" in idle_ratio_events([*fleet, newcomer])


# ---------------------------------------------------------------------------
# History task outcomes (I-5)
# ---------------------------------------------------------------------------


class TestHistoryStatus:
    def _task(self, started: bool, completed: float):
        return SimpleNamespace(
            actual_start=T0 if started else None, completed_quantity=completed, target_quantity=100.0
        )

    def test_finished_task_is_done(self):
        assert _history_status(self._task(True, 100.5)) == TaskStatus.DONE.value

    def test_never_started_task_is_cancelled(self):
        assert _history_status(self._task(False, 0.0)) == TaskStatus.CANCELLED.value

    def test_unfinished_task_is_not_closed(self):
        assert _history_status(self._task(True, 40.0)) == TaskStatus.PAUSED.value
