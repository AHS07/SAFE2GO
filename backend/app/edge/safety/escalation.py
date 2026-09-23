"""Repeat escalation tracker.

Rule: the 3rd WARNING incident of the same type within a rolling 30-minute
window (simulation clock) opens directly as CRITICAL.

Applies to: seatbelt, proximity, tilt, overload.
Does NOT apply to: working_condition, operator_not_seated, manual_report.

This tracker maintains a per-(machine, incident_type) deque of incident
open timestamps (sim-clock). On each new incident open, it:
  1. Trims entries older than the window.
  2. Checks whether the count has reached the threshold.
  3. Returns True if the new incident should open as CRITICAL.

The tracker never reads injected_anomalies and never touches the DB.
"""
from __future__ import annotations

from collections import defaultdict, deque

from app.config.thresholds import get_thresholds


class RepeatEscalationTracker:
    """Per-engine singleton that tracks WARNING counts per rule type."""

    def __init__(self) -> None:
        # Key: (machine_id, incident_type) -> deque of sim timestamps (float)
        self._windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def record_and_check(
        self,
        machine_id: str,
        incident_type: str,
        sim_ts: float,
        severity: str,
    ) -> bool:
        """Record a new incident open and return True if it should be CRITICAL.

        Only WARNING incidents are counted. CRITICAL incidents are not recorded
        (they already are at max severity).
        """
        cfg = get_thresholds().safety.repeat_escalation

        if incident_type not in cfg.applies_to:
            return False

        if severity != "warning":
            return False

        key = (machine_id, incident_type)
        window = self._windows[key]

        # Trim entries outside the rolling window.
        cutoff = sim_ts - cfg.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        window.append(sim_ts)

        # Threshold is 3: on the 3rd WARNING, escalate to CRITICAL.
        return len(window) >= cfg.count_threshold

    def reset_machine(self, machine_id: str) -> None:
        """Clear all counters for a machine (called on simulator reset)."""
        keys_to_remove = [k for k in self._windows if k[0] == machine_id]
        for key in keys_to_remove:
            del self._windows[key]
