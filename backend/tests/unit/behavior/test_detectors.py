"""Unit tests for all 5 behavior detectors.

Per rules.md §6 every detector needs:
  - A true positive (fires correctly)
  - A legitimate case that must NOT trigger
  - The specific edge case called out in phases.md (legit_wait / blocked idle)

All timestamps are simulation-clock datetimes. No wall-clock sleeps.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config.thresholds import get_thresholds
from app.edge.behavior.detectors.excessive_idling import ExcessiveIdlingDetector
from app.edge.behavior.detectors.high_rpm_travel import HighRpmTravelDetector
from app.edge.behavior.detectors.idle_ratio import IdleRatioDetector
from app.edge.behavior.detectors.repeated_overloading import RepeatedOverloadingDetector
from app.edge.behavior.detectors.repeated_seatbelt import RepeatedSeatbeltDetector
from app.shared.enums import BehaviorEventType, TaskStatus


def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


# ---------------------------------------------------------------------------
# Excessive idling
# ---------------------------------------------------------------------------

class TestExcessiveIdling:
    """Engine running, no hydraulics, speed 0 for >= 5 min fires."""

    def _run_idle_span(self, duration_s: int, task_status: str | None = None):
        det = ExcessiveIdlingDetector()
        event = None
        for i in range(duration_s + 1):
            event = det.update(
                tick_ts=_ts(float(i)),
                engine_running=True,
                hydraulic_active=False,
                machine_speed=0.0,
                task_status=task_status,
            )
        return event

    def test_fires_after_5_minutes(self):
        event = self._run_idle_span(300)   # 300 s = 5 min
        assert event is not None
        assert event.event_type == BehaviorEventType.EXCESSIVE_IDLING.value

    def test_does_not_fire_before_5_minutes(self):
        event = self._run_idle_span(299)
        assert event is None

    def test_hydraulic_active_resets_span(self):
        det = ExcessiveIdlingDetector()
        # 4 min idle
        for i in range(240):
            det.update(_ts(float(i)), True, False, 0.0, None)
        # one tick with hydraulics on — resets span
        det.update(_ts(240.0), True, True, 0.0, None)
        # another 4 min idle — should not fire (new span not yet 5 min)
        for i in range(241, 481):
            ev = det.update(_ts(float(i)), True, False, 0.0, None)
        assert ev is None

    def test_moving_machine_resets_span(self):
        det = ExcessiveIdlingDetector()
        for i in range(240):
            det.update(_ts(float(i)), True, False, 0.0, None)
        det.update(_ts(240.0), True, False, 1.0, None)   # machine moving
        ev = None
        for i in range(241, 481):
            ev = det.update(_ts(float(i)), True, False, 0.0, None)
        assert ev is None

    def test_engine_off_resets_span(self):
        det = ExcessiveIdlingDetector()
        for i in range(240):
            det.update(_ts(float(i)), True, False, 0.0, None)
        det.update(_ts(240.0), False, False, 0.0, None)   # engine off
        ev = None
        for i in range(241, 481):
            ev = det.update(_ts(float(i)), True, False, 0.0, None)
        assert ev is None

    def test_legit_wait_blocked_does_not_fire(self):
        """Blocked task status must exclude from idling span — legit_wait test."""
        event = self._run_idle_span(600, task_status=TaskStatus.BLOCKED.value)
        assert event is None

    def test_paused_status_does_count_as_idle(self):
        """Paused time IS included — only blocked is excluded."""
        event = self._run_idle_span(300, task_status=TaskStatus.PAUSED.value)
        assert event is not None

    def test_between_tasks_null_status_counts(self):
        """task_id = None (between tasks) counts as idling."""
        event = self._run_idle_span(300, task_status=None)
        assert event is not None

    def test_single_fire_not_duplicate(self):
        """A continuous 10-minute idle should fire exactly once."""
        det = ExcessiveIdlingDetector()
        events = []
        for i in range(601):
            ev = det.update(_ts(float(i)), True, False, 0.0, None)
            if ev:
                events.append(ev)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Repeated overloading
# ---------------------------------------------------------------------------

class TestRepeatedOverloading:
    def test_fires_on_third_incident(self):
        det = RepeatedOverloadingDetector()
        det.record_incident(_ts(0.0))
        det.record_incident(_ts(60.0))
        ev = det.record_incident(_ts(120.0))
        assert ev is not None
        assert ev.event_type == BehaviorEventType.REPEATED_OVERLOADING.value
        assert ev.magnitude == 3.0

    def test_does_not_fire_on_second_incident(self):
        det = RepeatedOverloadingDetector()
        det.record_incident(_ts(0.0))
        ev = det.record_incident(_ts(60.0))
        assert ev is None

    def test_does_not_fire_twice(self):
        det = RepeatedOverloadingDetector()
        det.record_incident(_ts(0.0))
        det.record_incident(_ts(60.0))
        det.record_incident(_ts(120.0))   # fires once
        ev = det.record_incident(_ts(180.0))   # fourth — should NOT fire again
        assert ev is None

    def test_single_incident_is_legit_not_pattern(self):
        det = RepeatedOverloadingDetector()
        ev = det.record_incident(_ts(0.0))
        assert ev is None

    def test_reset_clears_count(self):
        det = RepeatedOverloadingDetector()
        det.record_incident(_ts(0.0))
        det.record_incident(_ts(60.0))
        det.reset()
        det.record_incident(_ts(120.0))
        det.record_incident(_ts(180.0))
        ev = det.record_incident(_ts(240.0))
        assert ev is not None   # fires on 3rd after reset


# ---------------------------------------------------------------------------
# High-RPM travel
# ---------------------------------------------------------------------------

class TestHighRpmTravel:
    RATED_MAX = 1800.0   # excavator
    MACHINE_TYPE = "excavator"
    SPEED_THRESHOLD = 3.0   # km/h

    def _detector(self):
        return HighRpmTravelDetector(self.RATED_MAX, self.MACHINE_TYPE)

    def test_fires_after_60s_sustained(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.87   # 87% > 85%
        fired = None
        for i in range(62):
            ev = det.update(_ts(float(i)), rpm, 5.0)
            if ev is not None:
                fired = ev
        assert fired is not None
        assert fired.event_type == BehaviorEventType.HIGH_RPM_TRAVEL.value

    def test_does_not_fire_before_60s(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.87
        ev = None
        for i in range(59):
            ev = det.update(_ts(float(i)), rpm, 5.0)
        assert ev is None

    def test_low_rpm_does_not_trigger(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.80   # 80% < 85%
        fired = None
        for i in range(120):
            ev = det.update(_ts(float(i)), rpm, 5.0)
            if ev:
                fired = ev
        assert fired is None

    def test_slow_speed_does_not_trigger(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.87
        fired = None
        for i in range(120):
            ev = det.update(_ts(float(i)), rpm, 1.0)   # 1 km/h < 3 km/h threshold
            if ev:
                fired = ev
        assert fired is None

    def test_wheel_loader_uses_correct_threshold(self):
        """Wheel loader speed threshold is 15 km/h."""
        det = HighRpmTravelDetector(2100.0, "wheel_loader")
        rpm = 2100.0 * 0.87
        fired = None
        # Speed = 14 km/h — below wheel_loader threshold of 15 km/h, should NOT fire
        for i in range(120):
            ev = det.update(_ts(float(i)), rpm, 14.0)
            if ev:
                fired = ev
        assert fired is None

    def test_reset_clears_span(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.87
        for i in range(50):
            det.update(_ts(float(i)), rpm, 5.0)
        det.reset()
        fired = None
        for i in range(50, 110):
            ev = det.update(_ts(float(i)), rpm, 5.0)
            if ev:
                fired = ev
        assert fired is None   # new span only 60 ticks, fires at 61

    def test_interruption_resets_span(self):
        det = self._detector()
        rpm = self.RATED_MAX * 0.87
        for i in range(50):
            det.update(_ts(float(i)), rpm, 5.0)
        det.update(_ts(50.0), rpm, 0.0)   # stopped — resets span
        fired = None
        for i in range(51, 111):
            ev = det.update(_ts(float(i)), rpm, 5.0)
            if ev:
                fired = ev
        assert fired is None   # new span only 60 ticks


# ---------------------------------------------------------------------------
# Repeated seatbelt violations
# ---------------------------------------------------------------------------

class TestRepeatedSeatbelt:
    def test_fires_on_second_incident(self):
        det = RepeatedSeatbeltDetector()
        det.record_incident(_ts(0.0))
        ev = det.record_incident(_ts(300.0))
        assert ev is not None
        assert ev.event_type == BehaviorEventType.REPEATED_SEATBELT_VIOLATION.value
        assert ev.magnitude == 2.0

    def test_single_incident_is_not_pattern(self):
        det = RepeatedSeatbeltDetector()
        ev = det.record_incident(_ts(0.0))
        assert ev is None

    def test_does_not_fire_twice(self):
        det = RepeatedSeatbeltDetector()
        det.record_incident(_ts(0.0))
        det.record_incident(_ts(300.0))   # fires
        ev = det.record_incident(_ts(600.0))
        assert ev is None

    def test_reset_clears_count(self):
        det = RepeatedSeatbeltDetector()
        det.record_incident(_ts(0.0))
        det.reset()
        det.record_incident(_ts(300.0))
        ev = det.record_incident(_ts(600.0))
        assert ev is not None   # fires on 2nd after reset


# ---------------------------------------------------------------------------
# Idle ratio
# ---------------------------------------------------------------------------

class TestIdleRatio:
    START = datetime(2025, 1, 1, 7, 0, 0, tzinfo=UTC)
    END = datetime(2025, 1, 1, 15, 0, 0, tzinfo=UTC)
    MEDIAN = 0.20
    MAD = 0.03

    def threshold(self) -> float:
        # median + k x MAD, k from thresholds.yaml
        return self.MEDIAN + get_thresholds().behavior.idle_ratio_mad_multiplier * self.MAD

    def test_fires_when_above_median_plus_k_mad(self):
        det = IdleRatioDetector()
        ratio = self.threshold() + 0.01
        ev = det.check(self.START, self.END, idle_ratio=ratio,
                       baseline_median=self.MEDIAN, baseline_mad=self.MAD)
        assert ev is not None
        assert ev.event_type == BehaviorEventType.HIGH_IDLE_RATIO.value
        assert ev.magnitude == pytest.approx(ratio, abs=1e-4)

    def test_does_not_fire_at_threshold(self):
        det = IdleRatioDetector()
        ev = det.check(self.START, self.END, idle_ratio=round(self.threshold(), 4),
                       baseline_median=self.MEDIAN, baseline_mad=self.MAD)
        assert ev is None   # must be strictly greater than threshold

    def test_does_not_fire_below_threshold(self):
        det = IdleRatioDetector()
        ev = det.check(self.START, self.END, idle_ratio=0.22,
                       baseline_median=self.MEDIAN, baseline_mad=self.MAD)
        assert ev is None

    def test_baseline_value_set_on_event(self):
        det = IdleRatioDetector()
        ev = det.check(self.START, self.END, idle_ratio=0.95,
                       baseline_median=self.MEDIAN, baseline_mad=self.MAD)
        assert ev is not None
        assert ev.baseline_value == pytest.approx(self.threshold(), abs=1e-4)

    def test_zero_mad_does_not_blow_up(self):
        det = IdleRatioDetector()
        # If MAD is 0, threshold = median. ratio > median -> fires.
        ev = det.check(self.START, self.END, idle_ratio=0.21,
                       baseline_median=0.20, baseline_mad=0.0)
        assert ev is not None


# ---------------------------------------------------------------------------
# Baseline statistics: median + MAD
# ---------------------------------------------------------------------------

class TestBaselineStatistics:
    """Verify the statistical functions used by the baseline service."""

    def test_mad_formula(self):
        import numpy as np
        values = np.array([0.10, 0.15, 0.20, 0.25, 0.80])
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        assert median == pytest.approx(0.20)
        assert mad == pytest.approx(0.05)

    def test_mad_robust_to_outlier(self):
        """An outlier should not inflate the MAD the way std dev would."""
        import numpy as np
        normal = np.array([0.20] * 9 + [5.0])   # one extreme outlier
        mad = float(np.median(np.abs(normal - np.median(normal))))
        # With 9 values at 0.20 and one at 5.0, median=0.20, MAD=0.0
        assert mad == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Integration: legit_wait check (key exit criterion from phases.md)
# ---------------------------------------------------------------------------

def test_legit_wait_shifts_do_not_trigger_excessive_idling():
    """A shift with long engine-on blocked periods must not fire excessive_idling.

    This is the primary exit criterion from phases.md Phase 4.
    """
    det = ExcessiveIdlingDetector()
    # 30 minutes of engine running, speed=0, no hydraulics, but task is BLOCKED.
    events = []
    for i in range(1800):
        ev = det.update(
            tick_ts=_ts(float(i)),
            engine_running=True,
            hydraulic_active=False,
            machine_speed=0.0,
            task_status=TaskStatus.BLOCKED.value,
        )
        if ev:
            events.append(ev)
    assert len(events) == 0, "legit_wait (blocked) periods must not trigger excessive_idling"
