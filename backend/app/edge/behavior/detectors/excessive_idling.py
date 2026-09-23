"""Excessive idling detector.

Fires when the engine runs with no hydraulic activity and speed = 0
for a continuous span >= excessive_idling_seconds (default 5 min).

Excludes time when task status is 'blocked' (legitimate wait).
Includes paused time and between-task time (task_id = None).

The detector is tick-driven: call update() on every telemetry tick.
When the span threshold is crossed it returns a DetectionEvent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config.thresholds import get_thresholds
from app.shared.enums import BehaviorEventType, TaskStatus


@dataclass
class DetectionEvent:
    event_type: str
    start: datetime
    end: datetime
    magnitude: float        # idling duration in minutes
    baseline_value: float | None = None


class ExcessiveIdlingDetector:
    """Tracks continuous idling spans per shift.

    A new instance is created per shift (or per machine session).
    Call reset() when a new shift starts.
    """

    def __init__(self) -> None:
        self._span_start: datetime | None = None
        self._emitted_starts: set[datetime] = set()   # prevents duplicate events

    def update(
        self,
        tick_ts: datetime,
        engine_running: bool,
        hydraulic_active: bool,
        machine_speed: float,
        task_status: str | None,   # None = between tasks
    ) -> DetectionEvent | None:
        """Process one tick. Returns a DetectionEvent if the threshold is crossed."""
        threshold_s = get_thresholds().behavior.excessive_idling_seconds

        is_idling = (
            engine_running
            and not hydraulic_active
            and machine_speed == 0.0
            and task_status != TaskStatus.BLOCKED.value
        )

        if is_idling:
            if self._span_start is None:
                self._span_start = tick_ts
            else:
                span_s = (tick_ts - self._span_start).total_seconds()
                if span_s >= threshold_s and self._span_start not in self._emitted_starts:
                    self._emitted_starts.add(self._span_start)
                    return DetectionEvent(
                        event_type=BehaviorEventType.EXCESSIVE_IDLING.value,
                        start=self._span_start,
                        end=tick_ts,
                        magnitude=round(span_s / 60.0, 2),
                    )
        else:
            self._span_start = None

        return None

    def reset(self) -> None:
        self._span_start = None
        self._emitted_starts.clear()
