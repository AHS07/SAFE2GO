"""High idle ratio detector.

Fires at end-of-shift when the shift idle ratio exceeds:
  operator_baseline_median + 3 * MAD

Uses the fleet-wide baseline if the operator has fewer than
baseline_min_shifts in history.

Baseline values are passed in from the cached edge schema — this
detector never queries the DB itself.
"""
from __future__ import annotations

from datetime import datetime

from app.config.thresholds import get_thresholds
from app.edge.behavior.detectors.excessive_idling import DetectionEvent
from app.shared.enums import BehaviorEventType


class IdleRatioDetector:
    """End-of-shift idle ratio anomaly detector."""

    def check(
        self,
        shift_start: datetime,
        shift_end: datetime,
        idle_ratio: float,
        baseline_median: float,
        baseline_mad: float,
        mad_multiplier: float | None = None,
    ) -> DetectionEvent | None:
        """mad_multiplier overrides the configured value (threshold tuning only)."""
        multiplier = mad_multiplier if mad_multiplier is not None else get_thresholds().behavior.idle_ratio_mad_multiplier
        threshold = baseline_median + multiplier * baseline_mad

        if idle_ratio > threshold:
            return DetectionEvent(
                event_type=BehaviorEventType.HIGH_IDLE_RATIO.value,
                start=shift_start,
                end=shift_end,
                magnitude=round(idle_ratio, 4),
                baseline_value=round(threshold, 4),
            )
        return None
