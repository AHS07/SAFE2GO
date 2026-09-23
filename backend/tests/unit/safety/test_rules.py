"""Unit tests for all 7 safety rules.

Per rules.md §6 each rule must have tests for:
- trigger
- debounce / grace period
- hysteresis clear
- cooldown
- escalation to CRITICAL
- repeat escalation (where applicable)
- unknown / missing sensor value (treated as unknown, never safe)

All tests use the simulation clock directly — no wall-clock sleeps.
Timestamps are plain floats (Unix epoch seconds).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.edge.safety.escalation import RepeatEscalationTracker
from app.edge.safety.state_machine import RuleState
from app.schemas.telemetry import TelemetryTick

# ---------------------------------------------------------------------------
# Tick builder helper
# ---------------------------------------------------------------------------

def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def make_tick(**overrides: Any) -> TelemetryTick:
    defaults = dict(
        timestamp=_ts(0.0),
        machine_id="M001",
        operator_id="OP001",
        shift_id="SH001",
        engine_running=True,
        engine_rpm=1400.0,
        engine_hours=100.0,
        fuel_used=10.0,
        hydraulic_active=True,
        machine_speed=0.0,
        load_cycles=0,
        payload_pct=50.0,
        seatbelt_status="fastened",
        seat_occupied=True,
        park_brake=True,
        gear_state="park",
        ambient_temp=25.0,
        visibility=300.0,
        tilt_angle=5.0,
    )
    defaults.update(overrides)
    return TelemetryTick(**defaults)


# ---------------------------------------------------------------------------
# Seatbelt rule
# ---------------------------------------------------------------------------

class TestSeatbelt:
    def setup_method(self):
        from app.edge.safety.rules import seatbelt
        self.sm = seatbelt.build_state_machine()
        self.rule = seatbelt

    def _eval(self, ts_s: float, **tick_kw):
        tick = make_tick(timestamp=_ts(ts_s), **tick_kw)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_no_trigger_when_fastened(self):
        r = self._eval(0.0, seatbelt_status="fastened", park_brake=False, gear_state="forward")
        assert r.state == RuleState.IDLE

    def test_no_trigger_when_parked(self):
        # Park brake on — machine cannot move.
        r = self._eval(0.0, seatbelt_status="unfastened", park_brake=True, gear_state="park")
        assert r.state == RuleState.IDLE

    def test_grace_period_starts_pending(self):
        r = self._eval(0.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        assert r.state == RuleState.PENDING

    def test_warning_opens_after_grace(self):
        # warning_grace = 10 s
        self._eval(0.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        r = self._eval(10.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_escalates_after_grace(self):
        # critical_grace = 30 s
        self._eval(0.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        self._eval(10.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        r = self._eval(30.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        assert r.escalate_to_critical
        assert self.sm.current_severity == "critical"

    def test_clears_before_warning_grace(self):
        self._eval(0.0, seatbelt_status="unfastened", park_brake=False, gear_state="forward")
        self._eval(5.0, seatbelt_status="fastened", park_brake=False, gear_state="forward")
        assert self.sm.state == RuleState.IDLE

    def test_unknown_seatbelt_status_no_incident(self):
        # Unknown value — ingest rejects it, but rule should also not open.
        tick = make_tick(timestamp=_ts(0.0), seatbelt_status="unfastened",
                         park_brake=False, gear_state="forward")
        # Simulate unknown by bypassing validation
        object.__setattr__(tick, "seatbelt_status", "UNKNOWN_VALUE")
        r = self.rule.evaluate(self.sm, tick, 0.0)
        assert r.state == RuleState.IDLE

    def test_moving_by_speed_triggers_rule(self):
        # machine_speed > 0 counts as can-move even if park_brake is True
        self._eval(0.0, seatbelt_status="unfastened", park_brake=True,
                   gear_state="park", machine_speed=2.0)
        r = self._eval(10.0, seatbelt_status="unfastened", park_brake=True,
                       gear_state="park", machine_speed=2.0)
        assert r.opened


# ---------------------------------------------------------------------------
# Operator not seated rule
# ---------------------------------------------------------------------------

class TestOperatorNotSeated:
    def setup_method(self):
        from app.edge.safety.rules import operator_seated
        self.sm = operator_seated.build_state_machine()
        self.rule = operator_seated

    def _eval(self, ts_s: float, **tick_kw):
        tick = make_tick(timestamp=_ts(ts_s), **tick_kw)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_no_trigger_when_seated(self):
        r = self._eval(0.0, seat_occupied=True, engine_running=True, park_brake=False)
        assert r.state == RuleState.IDLE

    def test_no_trigger_when_parked(self):
        r = self._eval(0.0, seat_occupied=False, engine_running=True, park_brake=True)
        assert r.state == RuleState.IDLE

    def test_no_trigger_engine_off(self):
        r = self._eval(0.0, seat_occupied=False, engine_running=False, park_brake=False)
        assert r.state == RuleState.IDLE

    def test_grace_period_starts_pending(self):
        r = self._eval(0.0, seat_occupied=False, engine_running=True, park_brake=False)
        assert r.state == RuleState.PENDING

    def test_opens_as_critical_after_3s(self):
        self._eval(0.0, seat_occupied=False, engine_running=True, park_brake=False)
        r = self._eval(3.0, seat_occupied=False, engine_running=True, park_brake=False)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_clears_before_grace(self):
        self._eval(0.0, seat_occupied=False, engine_running=True, park_brake=False)
        self._eval(1.0, seat_occupied=True, engine_running=True, park_brake=False)
        assert self.sm.state == RuleState.IDLE


# ---------------------------------------------------------------------------
# Proximity rule
# ---------------------------------------------------------------------------

class TestProximity:
    def setup_method(self):
        from app.edge.safety.rules import proximity
        self.sm = proximity.build_state_machine()
        self.rule = proximity

    def _eval(self, ts_s: float, dist):
        tick = make_tick(timestamp=_ts(ts_s), proximity_distance=dist)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_no_object_no_trigger(self):
        r = self._eval(0.0, None)
        assert r.state == RuleState.IDLE

    def test_far_object_no_trigger(self):
        r = self._eval(0.0, 15.0)
        assert r.state == RuleState.IDLE

    def test_warning_distance_enters_pending(self):
        r = self._eval(0.0, 8.0)
        assert r.state == RuleState.PENDING

    def test_warning_opens_after_debounce(self):
        self._eval(0.0, 8.0)
        r = self._eval(2.0, 8.0)
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_distance_opens_critical(self):
        self._eval(0.0, 3.0)
        r = self._eval(2.0, 3.0)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_hysteresis_warning_clear(self):
        # Open warning, then distance rises above clear threshold (12 m)
        self._eval(0.0, 8.0)
        self._eval(2.0, 8.0)   # opens
        self._eval(3.0, 13.0)  # above clear (12 m) -> clearing
        r = self._eval(5.0, 13.0)  # 2 s hold -> closed
        assert r.closed

    def test_hysteresis_stays_active_between_trigger_and_clear(self):
        # Object at 8 m (warning), then moves to 11 m (between trigger 10 m and clear 12 m)
        self._eval(0.0, 8.0)
        self._eval(2.0, 8.0)   # opens warning
        r = self._eval(3.0, 11.0)  # still below warning_clear (12 m) -> stays active
        assert r.state == RuleState.ACTIVE

    def test_cooldown_after_close(self):
        self._eval(0.0, 8.0)
        self._eval(2.0, 8.0)
        self._eval(3.0, 13.0)
        self._eval(5.0, 13.0)   # closed
        self._eval(6.0, 8.0)    # immediate retrigger
        assert self.sm.state == RuleState.CLOSED  # still in cooldown


# ---------------------------------------------------------------------------
# Tilt rule
# ---------------------------------------------------------------------------

class TestTilt:
    TILT_LIMIT = 30.0  # degrees

    def setup_method(self):
        from app.edge.safety.rules import tilt
        self.sm = tilt.build_state_machine()
        self.rule = tilt

    def _eval(self, ts_s: float, angle: float):
        tick = make_tick(timestamp=_ts(ts_s), tilt_angle=angle)
        return self.rule.evaluate(self.sm, tick, ts_s, tilt_limit_degrees=self.TILT_LIMIT)

    def test_safe_tilt_no_trigger(self):
        r = self._eval(0.0, 20.0)   # 67% of limit
        assert r.state == RuleState.IDLE

    def test_warning_threshold_enters_pending(self):
        # 80% of 30 = 24 degrees
        r = self._eval(0.0, 24.0)
        assert r.state == RuleState.PENDING

    def test_warning_opens_after_debounce(self):
        self._eval(0.0, 24.0)
        r = self._eval(2.0, 24.0)
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_at_100_percent(self):
        # 100% of 30 = 30 degrees
        self._eval(0.0, 30.0)
        r = self._eval(2.0, 30.0)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_hysteresis_warning_clear_at_75_percent(self):
        # clear below 75% = 22.5 degrees
        self._eval(0.0, 24.0)
        self._eval(2.0, 24.0)    # opens warning
        self._eval(3.0, 22.0)    # below clear -> clearing
        r = self._eval(5.0, 22.0)
        assert r.closed

    def test_critical_clear_at_95_percent(self):
        # critical clear below 95% = 28.5 degrees
        self._eval(0.0, 30.0)
        self._eval(2.0, 30.0)    # opens critical
        self._eval(3.0, 28.0)    # below critical clear -> clearing
        r = self._eval(5.0, 28.0)
        assert r.closed


# ---------------------------------------------------------------------------
# Overload rule
# ---------------------------------------------------------------------------

class TestOverload:
    def setup_method(self):
        from app.edge.safety.rules import overload
        self.sm = overload.build_state_machine()
        self.rule = overload

    def _eval(self, ts_s: float, pct: float):
        tick = make_tick(timestamp=_ts(ts_s), payload_pct=pct)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_normal_load_no_trigger(self):
        r = self._eval(0.0, 90.0)
        assert r.state == RuleState.IDLE

    def test_warning_at_105(self):
        self._eval(0.0, 106.0)
        r = self._eval(2.0, 106.0)
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_at_120(self):
        self._eval(0.0, 122.0)
        r = self._eval(2.0, 122.0)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_hysteresis_warning_clears_below_100(self):
        self._eval(0.0, 106.0)
        self._eval(2.0, 106.0)   # opens
        self._eval(3.0, 99.0)    # below clear (100%) -> clearing
        r = self._eval(5.0, 99.0)
        assert r.closed

    def test_hysteresis_stays_active_between_105_and_100(self):
        self._eval(0.0, 106.0)
        self._eval(2.0, 106.0)   # opens warning
        r = self._eval(3.0, 102.0)  # between 100 and 105 — still active (warning_clear is 100%)
        assert r.state == RuleState.ACTIVE

    def test_critical_clear_at_110(self):
        self._eval(0.0, 122.0)
        self._eval(2.0, 122.0)   # opens critical
        self._eval(3.0, 108.0)   # below 110 -> clearing
        r = self._eval(5.0, 108.0)
        assert r.closed

    def test_escalation_warning_to_critical(self):
        self._eval(0.0, 106.0)
        self._eval(2.0, 106.0)   # opens warning
        r = self._eval(3.0, 122.0)  # now above critical threshold
        assert r.escalate_to_critical


# ---------------------------------------------------------------------------
# Visibility rule
# ---------------------------------------------------------------------------

class TestVisibility:
    def setup_method(self):
        from app.edge.safety.rules import visibility
        self.sm = visibility.build_state_machine()
        self.rule = visibility

    def _eval(self, ts_s: float, vis: float):
        tick = make_tick(timestamp=_ts(ts_s), visibility=vis)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_good_visibility_no_trigger(self):
        r = self._eval(0.0, 200.0)
        assert r.state == RuleState.IDLE

    def test_warning_below_100m(self):
        self._eval(0.0, 90.0)
        r = self._eval(2.0, 90.0)
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_below_50m(self):
        self._eval(0.0, 40.0)
        r = self._eval(2.0, 40.0)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_hysteresis_warning_clears_above_120m(self):
        self._eval(0.0, 90.0)
        self._eval(2.0, 90.0)
        self._eval(3.0, 130.0)   # above clear (120 m) -> clearing
        r = self._eval(5.0, 130.0)
        assert r.closed

    def test_hysteresis_stays_active_between_100_and_120(self):
        self._eval(0.0, 90.0)
        self._eval(2.0, 90.0)   # opens warning
        r = self._eval(3.0, 110.0)  # between 100 and 120 — still active
        assert r.state == RuleState.ACTIVE


# ---------------------------------------------------------------------------
# Temperature rule
# ---------------------------------------------------------------------------

class TestTemperature:
    def setup_method(self):
        from app.edge.safety.rules import temperature
        self.sm = temperature.build_state_machine()
        self.rule = temperature

    def _eval(self, ts_s: float, temp: float):
        tick = make_tick(timestamp=_ts(ts_s), ambient_temp=temp)
        return self.rule.evaluate(self.sm, tick, ts_s)

    def test_normal_temp_no_trigger(self):
        r = self._eval(0.0, 30.0)
        assert r.state == RuleState.IDLE

    def test_warning_at_35(self):
        self._eval(0.0, 36.0)
        r = self._eval(2.0, 36.0)
        assert r.opened
        assert self.sm.current_severity == "warning"

    def test_critical_at_40(self):
        self._eval(0.0, 41.0)
        r = self._eval(2.0, 41.0)
        assert r.opened
        assert self.sm.current_severity == "critical"

    def test_hysteresis_warning_clears_below_33(self):
        self._eval(0.0, 36.0)
        self._eval(2.0, 36.0)
        self._eval(3.0, 32.0)   # below clear (33 C) -> clearing
        r = self._eval(5.0, 32.0)
        assert r.closed

    def test_stays_active_between_33_and_35(self):
        self._eval(0.0, 36.0)
        self._eval(2.0, 36.0)   # opens warning
        r = self._eval(3.0, 34.0)  # between 33 and 35 — still active
        assert r.state == RuleState.ACTIVE

    def test_critical_clear_below_38(self):
        self._eval(0.0, 41.0)
        self._eval(2.0, 41.0)   # critical
        self._eval(3.0, 37.0)   # below 38 -> clearing
        r = self._eval(5.0, 37.0)
        assert r.closed


# ---------------------------------------------------------------------------
# Repeat escalation tracker
# ---------------------------------------------------------------------------

class TestRepeatEscalation:
    def setup_method(self):
        self.tracker = RepeatEscalationTracker()

    def test_first_warning_no_escalation(self):
        result = self.tracker.record_and_check("M001", "proximity", 0.0, "warning")
        assert result is False

    def test_second_warning_no_escalation(self):
        self.tracker.record_and_check("M001", "proximity", 0.0, "warning")
        result = self.tracker.record_and_check("M001", "proximity", 60.0, "warning")
        assert result is False

    def test_third_warning_triggers_escalation(self):
        self.tracker.record_and_check("M001", "proximity", 0.0, "warning")
        self.tracker.record_and_check("M001", "proximity", 60.0, "warning")
        result = self.tracker.record_and_check("M001", "proximity", 120.0, "warning")
        assert result is True

    def test_outside_window_resets_count(self):
        # window = 1800 s
        self.tracker.record_and_check("M001", "proximity", 0.0, "warning")
        self.tracker.record_and_check("M001", "proximity", 60.0, "warning")
        # Third warning is outside the 1800-s window
        result = self.tracker.record_and_check("M001", "proximity", 2000.0, "warning")
        assert result is False

    def test_critical_incidents_not_counted(self):
        self.tracker.record_and_check("M001", "proximity", 0.0, "warning")
        self.tracker.record_and_check("M001", "proximity", 60.0, "warning")
        # Critical is not counted
        result = self.tracker.record_and_check("M001", "proximity", 120.0, "critical")
        assert result is False

    def test_does_not_apply_to_working_condition(self):
        for ts in [0.0, 60.0, 120.0]:
            result = self.tracker.record_and_check("M001", "working_condition", ts, "warning")
        assert result is False

    def test_does_not_apply_to_manual_report(self):
        for ts in [0.0, 60.0, 120.0]:
            result = self.tracker.record_and_check("M001", "manual_report", ts, "warning")
        assert result is False

    def test_independent_per_machine(self):
        self.tracker.record_and_check("M001", "overload", 0.0, "warning")
        self.tracker.record_and_check("M001", "overload", 60.0, "warning")
        # Third for M002 — should not escalate (only 1 for M002)
        result = self.tracker.record_and_check("M002", "overload", 120.0, "warning")
        assert result is False

    def test_reset_clears_machine_counters(self):
        self.tracker.record_and_check("M001", "seatbelt", 0.0, "warning")
        self.tracker.record_and_check("M001", "seatbelt", 60.0, "warning")
        self.tracker.reset_machine("M001")
        result = self.tracker.record_and_check("M001", "seatbelt", 120.0, "warning")
        assert result is False  # counter reset, only 1 now


# ---------------------------------------------------------------------------
# Single continuous hazard produces ONE incident (not one per tick)
# ---------------------------------------------------------------------------

class TestSingleIncidentPerHazard:
    """A 30-second continuous hazard at 1 Hz must produce exactly one opened=True."""

    def test_proximity_30s_one_incident(self):
        from app.edge.safety.rules import proximity
        sm = proximity.build_state_machine()
        opened_count = 0
        for i in range(31):
            tick = make_tick(timestamp=_ts(float(i)), proximity_distance=4.0)
            r = proximity.evaluate(sm, tick, float(i))
            if r.opened:
                opened_count += 1
        assert opened_count == 1

    def test_overload_30s_one_incident(self):
        from app.edge.safety.rules import overload
        sm = overload.build_state_machine()
        opened_count = 0
        for i in range(31):
            tick = make_tick(timestamp=_ts(float(i)), payload_pct=110.0)
            r = overload.evaluate(sm, tick, float(i))
            if r.opened:
                opened_count += 1
        assert opened_count == 1
