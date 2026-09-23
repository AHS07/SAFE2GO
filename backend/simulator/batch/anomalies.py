"""Anomaly injection for the synthetic dataset.

Injects ~50 labeled anomalies and ~50 legit-wait shifts into the
injected_anomaly ground-truth table. The detection engine never reads
this table — it is only used by evaluation/detection_report.py.

Anomaly types injected:
  excessive_idling        — long engine-on, no work window
  repeated_overloading    — 3+ overload events within a shift
  high_rpm_travel         — high RPM while travelling
  repeated_seatbelt_violation — 2+ seatbelt-off windows
  high_idle_ratio         — shift idle fraction above baseline

Legit-wait shifts:
  legit_wait              — long blocked windows; engine running but
                            correctly not flagged as excessive idling
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.shared.enums import AnomalyType
from simulator.batch.shifts import ShiftRecord

N_ANOMALY_SHIFTS = 50
N_LEGIT_WAIT_SHIFTS = 50

# Days 41-50 are the test split — we inject anomalies across both splits
# but track them all so evaluation can filter by split.
_ANOMALY_TYPES = [
    AnomalyType.EXCESSIVE_IDLING,
    AnomalyType.REPEATED_OVERLOADING,
    AnomalyType.HIGH_RPM_TRAVEL,
    AnomalyType.REPEATED_SEATBELT_VIOLATION,
    AnomalyType.HIGH_IDLE_RATIO,
]


@dataclass
class AnomalyRecord:
    anomaly_id: str
    shift_id: str
    injected_anomaly_type: str
    event_start: datetime
    event_end: datetime
    notes: str


def inject_anomalies(
    shifts: list[ShiftRecord],
    rng: np.random.Generator,
) -> tuple[list[AnomalyRecord], dict[str, list[tuple[datetime, datetime, str]]]]:
    """Choose shifts for anomaly injection.

    Returns:
        anomaly_records: rows to insert into cloud.injected_anomaly
        anomaly_windows: dict of shift_id -> list of (start, end, type)
            passed to the telemetry generator so it produces matching sensor
            readings.
    """
    anomaly_records: list[AnomalyRecord] = []
    # shift_id -> [(start, end, type), ...]
    anomaly_windows: dict[str, list[tuple[datetime, datetime, str]]] = {}

    # Select non-overlapping shifts for injection
    eligible = [s for s in shifts if len(s.tasks) >= 2]
    chosen_indices = rng.choice(len(eligible), size=min(N_ANOMALY_SHIFTS, len(eligible)), replace=False)

    for idx in chosen_indices:
        shift = eligible[int(idx)]
        atype = _ANOMALY_TYPES[int(rng.integers(0, len(_ANOMALY_TYPES)))]
        windows = _build_windows(shift, atype, rng)

        for start, end in windows:
            rec = AnomalyRecord(
                anomaly_id=str(uuid.uuid4()),
                shift_id=shift.shift_id,
                injected_anomaly_type=atype.value,
                event_start=start,
                event_end=end,
                notes=f"Injected {atype.value} for evaluation.",
            )
            anomaly_records.append(rec)
            anomaly_windows.setdefault(shift.shift_id, []).append((start, end, atype.value))

    # Legit-wait shifts: mark them so evaluation knows not to flag them
    legit_eligible = [s for s in shifts if s.shift_id not in anomaly_windows and len(s.tasks) >= 2]
    legit_indices = rng.choice(
        len(legit_eligible), size=min(N_LEGIT_WAIT_SHIFTS, len(legit_eligible)), replace=False
    )

    for idx in legit_indices:
        shift = legit_eligible[int(idx)]
        # A legit-wait has a long blocked window mid-shift
        mid = shift.scheduled_start + (shift.scheduled_end - shift.scheduled_start) / 2
        start = mid - timedelta(minutes=30)
        end = mid + timedelta(minutes=30)
        rec = AnomalyRecord(
            anomaly_id=str(uuid.uuid4()),
            shift_id=shift.shift_id,
            injected_anomaly_type=AnomalyType.LEGIT_WAIT.value,
            event_start=start,
            event_end=end,
            notes="Legitimate blocked wait — engine running, task blocked.",
        )
        anomaly_records.append(rec)
        # Legit-wait windows are NOT added to anomaly_windows — the telemetry
        # generator will treat the machine as blocked (not idling) during these.
        anomaly_windows.setdefault(shift.shift_id, []).append(
            (start, end, "legit_wait")
        )

    return anomaly_records, anomaly_windows


def _build_windows(
    shift: ShiftRecord,
    atype: AnomalyType,
    rng: np.random.Generator,
) -> list[tuple[datetime, datetime]]:
    """Return (start, end) windows for a given anomaly type in a shift."""
    shift_duration = (shift.scheduled_end - shift.scheduled_start).total_seconds()

    if atype == AnomalyType.EXCESSIVE_IDLING:
        # One window of 6-15 minutes between tasks
        offset = float(rng.uniform(0.1, 0.5)) * shift_duration
        start = shift.scheduled_start + timedelta(seconds=offset)
        duration = float(rng.uniform(360, 900))   # 6-15 min in seconds
        return [(start, start + timedelta(seconds=duration))]

    if atype == AnomalyType.REPEATED_OVERLOADING:
        # 3-4 overload events spread through the shift
        windows = []
        for i in range(int(rng.integers(3, 5))):
            offset = float(rng.uniform(0.1 + i * 0.15, 0.2 + i * 0.18)) * shift_duration
            start = shift.scheduled_start + timedelta(seconds=offset)
            duration = float(rng.uniform(60, 180))   # 1-3 min
            windows.append((start, start + timedelta(seconds=duration)))
        return windows

    if atype == AnomalyType.HIGH_RPM_TRAVEL:
        # One sustained high-RPM travel window of 60-120 seconds
        offset = float(rng.uniform(0.2, 0.7)) * shift_duration
        start = shift.scheduled_start + timedelta(seconds=offset)
        duration = float(rng.uniform(65, 120))
        return [(start, start + timedelta(seconds=duration))]

    if atype == AnomalyType.REPEATED_SEATBELT_VIOLATION:
        # 2-3 seatbelt-off windows
        windows = []
        for i in range(int(rng.integers(2, 4))):
            offset = float(rng.uniform(0.1 + i * 0.2, 0.2 + i * 0.25)) * shift_duration
            start = shift.scheduled_start + timedelta(seconds=offset)
            duration = float(rng.uniform(35, 90))   # long enough to trigger WARNING
            windows.append((start, start + timedelta(seconds=duration)))
        return windows

    if atype == AnomalyType.HIGH_IDLE_RATIO:
        # Several idling windows totalling > 30% of shift time
        windows = []
        for i in range(3):
            offset = float(rng.uniform(0.05 + i * 0.25, 0.15 + i * 0.28)) * shift_duration
            start = shift.scheduled_start + timedelta(seconds=offset)
            duration = float(rng.uniform(600, 1500))   # 10-25 min
            windows.append((start, start + timedelta(seconds=duration)))
        return windows

    return []
