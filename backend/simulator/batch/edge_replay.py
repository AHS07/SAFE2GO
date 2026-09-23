"""Replay batch history through the edge behavior detectors.

The edge produces behavior events live. For the historical dataset the
generator replays each shift's ticks through the same detectors, so
evaluation/detection_report.py can measure them against the injected
ground truth. The replay never sees the ground truth: it only gets raw
ticks plus the task status the edge task store would have held (blocked
or paused), which the generator knows from its own plan.

Repeated overloading and repeated seatbelt violations count incidents,
so the seatbelt and overload safety rules run here as well. Incidents
themselves are not stored for history.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.config.thresholds import get_thresholds
from app.edge.behavior.detectors.excessive_idling import DetectionEvent, ExcessiveIdlingDetector
from app.edge.behavior.detectors.high_rpm_travel import HighRpmTravelDetector
from app.edge.behavior.detectors.idle_ratio import IdleRatioDetector
from app.edge.behavior.detectors.repeated_overloading import RepeatedOverloadingDetector
from app.edge.behavior.detectors.repeated_seatbelt import RepeatedSeatbeltDetector
from app.edge.safety.rules import overload, seatbelt
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import TaskStatus

_TICK_FIELDS = tuple(TelemetryTick.model_fields)


def _task_status(tick: dict) -> str | None:
    # Imported here to avoid a cycle: telemetry.py imports this package.
    from simulator.batch.telemetry import _Phase

    if tick["task_id"] is None:
        return None
    phase = tick["_phase"]
    if phase == _Phase.BLOCKED:
        return TaskStatus.BLOCKED.value
    if phase == _Phase.PAUSED:
        return TaskStatus.PAUSED.value
    return TaskStatus.IN_PROGRESS.value


def replay_shift(
    ticks: Iterable[dict], rated_max_rpm: float, machine_type: str
) -> list[DetectionEvent]:
    """Tick-driven and incident-driven behavior events for one shift."""
    idling = ExcessiveIdlingDetector()
    high_rpm = HighRpmTravelDetector(rated_max_rpm, machine_type)
    repeated_overload = RepeatedOverloadingDetector()
    repeated_seatbelt = RepeatedSeatbeltDetector()
    seatbelt_rule = seatbelt.build_state_machine()
    overload_rule = overload.build_state_machine()

    events: list[DetectionEvent] = []
    for raw in ticks:
        tick = TelemetryTick.model_construct(**{f: raw[f] for f in _TICK_FIELDS})
        ts = tick.timestamp
        sim_ts = ts.timestamp()

        candidates = [
            idling.update(ts, tick.engine_running, tick.hydraulic_active, tick.machine_speed, _task_status(raw)),
            high_rpm.update(ts, tick.engine_rpm, tick.machine_speed),
        ]
        if seatbelt.evaluate(seatbelt_rule, tick, sim_ts).opened:
            candidates.append(repeated_seatbelt.record_incident(ts))
        if overload.evaluate(overload_rule, tick, sim_ts).opened:
            candidates.append(repeated_overload.record_incident(ts))
        events.extend(e for e in candidates if e is not None)
    return events


@dataclass(frozen=True)
class ShiftIdleRecord:
    shift_id: str
    operator_id: str
    machine_type: str
    start: datetime
    end: datetime
    idle_ratio: float
    in_baseline_window: bool      # shifts before the evaluation split build the baselines


def _mad(values: list[float]) -> float:
    arr = np.asarray(values)
    return float(np.median(np.abs(arr - np.median(arr))))


def idle_ratio_events(
    records: list[ShiftIdleRecord], mad_multiplier: float | None = None
) -> dict[str, DetectionEvent]:
    """high_idle_ratio events per shift_id, judged against baseline-window history.

    Operator baseline per machine type when the operator has enough shifts,
    otherwise the fleet baseline for that machine type (median and MAD).
    """
    min_shifts = get_thresholds().behavior.baseline_min_shifts
    by_operator: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_fleet: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.in_baseline_window:
            by_operator[(r.operator_id, r.machine_type)].append(r.idle_ratio)
            by_fleet[r.machine_type].append(r.idle_ratio)

    detector = IdleRatioDetector()
    events: dict[str, DetectionEvent] = {}
    for r in records:
        history = by_operator.get((r.operator_id, r.machine_type), [])
        if len(history) < min_shifts:
            history = by_fleet.get(r.machine_type, [])
        if not history:
            continue
        event = detector.check(
            shift_start=r.start,
            shift_end=r.end,
            idle_ratio=r.idle_ratio,
            baseline_median=float(np.median(history)),
            baseline_mad=_mad(history),
            mad_multiplier=mad_multiplier,
        )
        if event is not None:
            events[r.shift_id] = event
    return events
