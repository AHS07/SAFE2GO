"""Repeated overloading detector.

Fires when the shift accumulates >= 3 overload incidents (WARNING or CRITICAL).
A single overload crossing is a Safety incident; repetition is the behavior
pattern this detector surfaces as coaching.

This detector is incident-driven: call record_incident() each time a new
overload incident opens. It is NOT tick-driven.
"""
from __future__ import annotations

from datetime import datetime

from app.config.thresholds import get_thresholds
from app.edge.behavior.detectors.excessive_idling import DetectionEvent
from app.shared.enums import BehaviorEventType


class RepeatedOverloadingDetector:
    """Counts overload incidents within a shift."""

    def __init__(self) -> None:
        self._count: int = 0
        self._first_ts: datetime | None = None
        self._last_ts: datetime | None = None
        self._emitted: bool = False

    def record_incident(self, event_ts: datetime) -> DetectionEvent | None:

        threshold = get_thresholds().behavior.repeated_overloading_count
        self._count += 1

        if self._first_ts is None:
            self._first_ts = event_ts
        self._last_ts = event_ts

        if self._count >= threshold and not self._emitted:
            self._emitted = True
            return DetectionEvent(
                event_type=BehaviorEventType.REPEATED_OVERLOADING.value,
                start=self._first_ts,
                end=self._last_ts,
                magnitude=float(self._count),
            )
        return None

    def reset(self) -> None:
        self._count = 0
        self._first_ts = None
        self._last_ts = None
        self._emitted = False
