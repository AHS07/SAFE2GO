"""Fixes from the session 12 audit: proximity sensor fault is unknown, never clear;
engine checkpoints; tick durations; telemetry contract."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.edge.behavior.engine import BehaviorEngine
from app.edge.safety.engine import SafetyEngine
from app.edge.safety.rules import proximity
from app.edge.safety.state_machine import RuleState
from tests.unit.safety.test_rules import _ts, make_tick

T0 = datetime(2030, 1, 7, 7, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Proximity sensor fault (finding 2, PRD F2.9)
# ---------------------------------------------------------------------------


def test_telemetry_must_state_proximity_sensor_health() -> None:
    with pytest.raises(ValidationError):
        make_tick(proximity_sensor_ok=None)


def test_working_sensor_with_no_distance_means_nothing_in_range() -> None:
    machine = proximity.build_state_machine()
    result = proximity.evaluate(machine, make_tick(proximity_distance=None, proximity_sensor_ok=True), 0.0)
    assert result.unknown is False and machine.state == RuleState.IDLE


def test_sensor_fault_is_unknown_and_opens_nothing() -> None:
    machine = proximity.build_state_machine()
    for second in range(10):
        result = proximity.evaluate(
            machine, make_tick(timestamp=_ts(second), proximity_distance=None, proximity_sensor_ok=False), float(second)
        )
        assert result.unknown and not result.opened and not result.closed
    assert machine.state == RuleState.IDLE


def _active_critical(machine) -> None:  # noqa: ANN001 - state machine
    for second in range(4):
        proximity.evaluate(machine, make_tick(timestamp=_ts(second), proximity_distance=3.0), float(second))
    assert machine.state == RuleState.ACTIVE


def test_sensor_fault_keeps_an_active_hazard_open() -> None:
    machine = proximity.build_state_machine()
    _active_critical(machine)
    for second in range(4, 30):
        result = proximity.evaluate(
            machine, make_tick(timestamp=_ts(second), proximity_distance=None, proximity_sensor_ok=False), float(second)
        )
        assert not result.closed
    assert machine.state == RuleState.ACTIVE


def test_sensor_fault_resets_a_clear_that_was_being_confirmed() -> None:
    machine = proximity.build_state_machine()
    _active_critical(machine)
    proximity.evaluate(machine, make_tick(timestamp=_ts(4), proximity_distance=20.0), 4.0)
    assert machine.state == RuleState.CLEARING
    proximity.evaluate(machine, make_tick(timestamp=_ts(5), proximity_distance=None, proximity_sensor_ok=False), 5.0)
    assert machine.state == RuleState.ACTIVE
    # One good clear reading after the fault is not enough: the clear hold starts again.
    result = proximity.evaluate(machine, make_tick(timestamp=_ts(6), proximity_distance=20.0), 6.0)
    assert not result.closed and machine.state == RuleState.CLEARING


class _Sent:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, machine_id: str, message: dict) -> None:
        self.messages.append(message)


async def test_engine_reports_sensor_fault_then_restoration_once_each() -> None:
    engine = SafetyEngine("M001", 30.0)
    sent = _Sent()
    engine.set_ws_broadcast(sent)
    for second, ok in enumerate([True, False, False, False, True, True]):
        await engine.process_tick(
            make_tick(timestamp=_ts(second), proximity_distance=None, proximity_sensor_ok=ok), session=None
        )
        await engine.flush_effects()
    kinds = [(m["type"], m["data"].get("reason")) for m in sent.messages]
    assert kinds == [("safety_degraded", "sensor_fault"), ("safety_restored", None)]


# ---------------------------------------------------------------------------
# Engine checkpoints (finding 1)
# ---------------------------------------------------------------------------


async def test_safety_restore_discards_state_and_queued_messages() -> None:
    engine = SafetyEngine("M001", 30.0)
    sent = _Sent()
    engine.set_ws_broadcast(sent)
    saved = engine.checkpoint()
    await engine.process_tick(make_tick(proximity_distance=None, proximity_sensor_ok=False), session=None)
    engine.restore(saved)
    await engine.flush_effects()
    assert sent.messages == []
    proximity_slot = next(s for s in engine._rules if s.name == "proximity")
    assert proximity_slot.unknown is False


def _behavior() -> BehaviorEngine:
    return BehaviorEngine(
        machine_id="M001", operator_id="OP001", shift_id="SH001", rated_max_rpm=1800.0,
        machine_type="excavator", baseline_median=0.2, baseline_mad=0.05, shift_start=T0,
    )


async def test_behavior_restore_undoes_a_shift_end() -> None:
    engine = _behavior()
    saved = engine.checkpoint()
    assert await engine.on_shift_end(T0 + timedelta(hours=1), session=None) is True
    engine.restore(saved)
    assert engine.shift_ended is False


# ---------------------------------------------------------------------------
# Tick durations (finding 4)
# ---------------------------------------------------------------------------


async def test_idle_time_follows_the_gap_between_ticks() -> None:
    engine = _behavior()
    idle = {"hydraulic_active": False, "machine_speed": 0.0}
    for second in (0, 2, 4, 6):          # a sensor reporting every 2 s
        await engine.process_tick(make_tick(timestamp=T0 + timedelta(seconds=second), **idle), None, session=None)
    # The first tick counts as one nominal tick (1 s), each later one as 2 s.
    assert engine._total_engine_seconds == pytest.approx(7.0)
    assert engine._total_idle_seconds == pytest.approx(7.0)


async def test_a_pause_in_the_stream_is_not_counted_as_engine_time() -> None:
    engine = _behavior()
    for second in (0, 1, 600):           # the stream stops for ten minutes
        await engine.process_tick(make_tick(timestamp=T0 + timedelta(seconds=second)), None, session=None)
    assert engine._total_engine_seconds == pytest.approx(1.0 + 1.0 + 5.0)


async def test_blocked_idle_time_is_consistent_in_idle_ratio_but_not_excessive_idling() -> None:
    engine = _behavior()
    for second in range(400):
        await engine.process_tick(
            make_tick(timestamp=T0 + timedelta(seconds=second), hydraulic_active=False, machine_speed=0.0),
            "blocked",
            session=None,
        )
    assert engine._total_idle_seconds == pytest.approx(engine._total_engine_seconds)


async def test_unresolved_incident_tracking_can_move_to_next_shift() -> None:
    from simulator.live.clock_driver import ClockDriver

    driver = ClockDriver(selected_speed=12)
    previous, current = SafetyEngine("M001", 30.0), SafetyEngine("M001", 30.0)
    previous.set_clock_driver(driver)
    current.set_clock_driver(driver)
    previous._unresolved.add("incident-1")
    driver.notify_incident_opened()

    current.adopt_unresolved(previous.release_unresolved())
    assert driver.effective_speed == 1
    await current.incident_resolved("incident-1")
    assert driver.effective_speed == 12


async def test_dead_letter_replay_uses_original_sequence() -> None:
    from types import SimpleNamespace

    from app.db.models.cloud.sync import CloudInbox, CloudOutbox
    from app.sync.worker import Direction, _apply

    received: list[int] = []

    async def handler(session, payload, seq):  # noqa: ANN001, ANN202 - test handler
        received.append(seq)

    class FakeSession:
        def __init__(self) -> None:
            self.added = []

        async def get(self, model, key):  # noqa: ANN001, ANN202 - AsyncSession fake
            return None

        def add(self, row):  # noqa: ANN001, ANN201 - AsyncSession fake
            self.added.append(row)

    direction = Direction("test", CloudOutbox, CloudInbox, None, {"test": handler})
    message = SimpleNamespace(
        idempotency_key="idempotency", message_type="test", payload="{}", seq=900,
        replay_seq=42, delivered_at=None,
    )
    session = FakeSession()
    await _apply(session, direction, message)
    assert received == [42]
    assert session.added[0].seq == 42
