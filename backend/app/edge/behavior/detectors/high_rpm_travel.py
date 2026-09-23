"""High-RPM travel detector.

Fires when the machine travels above the type-specific speed threshold
with engine RPM >= 85% of rated_max_rpm for a sustained span >= 60 s.

Parameters provided at construction:
  rated_max_rpm:   from machine master data
  machine_type:    determines speed threshold from config
"""
from __future__ import annotations

from datetime import datetime

from app.config.thresholds import get_thresholds
from app.edge.behavior.detectors.excessive_idling import DetectionEvent
from app.shared.enums import BehaviorEventType, MachineType


class HighRpmTravelDetector:
    """Detects sustained high-RPM travel per shift."""

    def __init__(self, rated_max_rpm: float, machine_type: str) -> None:
        self._rated_max_rpm = rated_max_rpm
        self._machine_type = machine_type
        self._span_start: datetime | None = None
        self._emitted_starts: set[datetime] = set()

    def update(
        self,
        tick_ts: datetime,
        engine_rpm: float,
        machine_speed: float,
    ) -> DetectionEvent | None:

        cfg = get_thresholds().behavior
        threshold_s = cfg.high_rpm_travel_sustained_seconds
        rpm_threshold = self._rated_max_rpm * cfg.high_rpm_fraction

        speed_thresholds = cfg.travel_speed_threshold_kmh
        if self._machine_type == MachineType.EXCAVATOR.value:
            speed_threshold = speed_thresholds.excavator
        elif self._machine_type == MachineType.WHEEL_LOADER.value:
            speed_threshold = speed_thresholds.wheel_loader
        else:
            speed_threshold = speed_thresholds.backhoe_loader

        condition = engine_rpm >= rpm_threshold and machine_speed >= speed_threshold

        if condition:
            if self._span_start is None:
                self._span_start = tick_ts
            else:
                span_s = (tick_ts - self._span_start).total_seconds()
                if span_s >= threshold_s and self._span_start not in self._emitted_starts:
                    self._emitted_starts.add(self._span_start)
                    return DetectionEvent(
                        event_type=BehaviorEventType.HIGH_RPM_TRAVEL.value,
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
