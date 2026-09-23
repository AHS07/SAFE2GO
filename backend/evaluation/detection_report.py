"""Behavior anomaly detection evaluation report.

Compares behavior events produced by the edge detectors (edge schema,
written for history by the batch generator's edge replay) with the
labeled ground truth in cloud.injected_anomaly.

This is the ONLY module allowed to read cloud.injected_anomaly.

Scoring, on the held-out split (shifts from EVALUATION_SPLIT_START):
- One injected anomaly is one (shift, type) pair. Repeated-pattern
  anomalies are stored as several windows (for example three overload
  windows); together they are one anomaly.
- Detected: an event of the anomaly's type overlaps any of its windows.
- False positive: an event that overlaps no injected window that explains
  it. An excessive_idling event inside an injected high_idle_ratio window
  is explained (those windows are long idle periods by construction) and
  is reported separately rather than counted as a false positive.
- Legit-wait false positives (excessive_idling on a legit_wait shift) are
  reported on their own line because they must be zero.

Run:
    python -m evaluation.detection_report
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.core.logging import setup_logging
from app.db.models.cloud.analytics import InjectedAnomaly
from app.db.models.cloud.shift import Shift
from app.db.models.edge.behavior import BehaviorEvent
from app.shared.enums import AnomalyType, BehaviorEventType
from simulator.batch.shifts import EVALUATION_SPLIT_START

log = logging.getLogger("safe2go.detection_report")

# Injected types whose windows also explain an event of another type.
_EXPLAINS: dict[str, set[str]] = {
    AnomalyType.HIGH_IDLE_RATIO.value: {BehaviorEventType.EXCESSIVE_IDLING.value},
}


@dataclass(frozen=True)
class Window:
    shift_id: str
    kind: str
    start: datetime
    end: datetime


@dataclass
class DetectionResult:
    detected: Counter = field(default_factory=Counter)
    missed: Counter = field(default_factory=Counter)
    false_positives: Counter = field(default_factory=Counter)
    explained: Counter = field(default_factory=Counter)
    legit_wait_false_positives: int = 0
    legit_wait_shifts: int = 0

    @property
    def total_detected(self) -> int:
        return sum(self.detected.values())

    @property
    def total_injected(self) -> int:
        return self.total_detected + sum(self.missed.values())

    @property
    def total_false_positives(self) -> int:
        return sum(self.false_positives.values())


def _overlaps(a: Window, b: Window) -> bool:
    return a.shift_id == b.shift_id and a.start < b.end and b.start < a.end


def evaluate(anomalies: list[Window], events: list[Window]) -> DetectionResult:
    """Score detected events against injected windows. Pure function."""
    result = DetectionResult()
    injected = [a for a in anomalies if a.kind != AnomalyType.LEGIT_WAIT.value]
    legit_wait_shifts = {a.shift_id for a in anomalies if a.kind == AnomalyType.LEGIT_WAIT.value}
    result.legit_wait_shifts = len(legit_wait_shifts)

    grouped: dict[tuple[str, str], list[Window]] = {}
    for window in injected:
        grouped.setdefault((window.shift_id, window.kind), []).append(window)
    for (_, kind), windows in grouped.items():
        hit = any(e.kind == kind and _overlaps(w, e) for w in windows for e in events)
        (result.detected if hit else result.missed)[kind] += 1

    for event in events:
        if any(a.kind == event.kind and _overlaps(a, event) for a in injected):
            continue
        if any(event.kind in _EXPLAINS.get(a.kind, set()) and _overlaps(a, event) for a in injected):
            result.explained[event.kind] += 1
            continue
        result.false_positives[event.kind] += 1
        if event.shift_id in legit_wait_shifts and event.kind == BehaviorEventType.EXCESSIVE_IDLING.value:
            result.legit_wait_false_positives += 1
    return result


async def load_windows(session: AsyncSession) -> tuple[list[Window], list[Window]]:
    shift_ids = set(
        (await session.execute(
            select(Shift.shift_id).where(Shift.scheduled_start >= EVALUATION_SPLIT_START)
        )).scalars().all()
    )
    if not shift_ids:
        return [], []

    anomalies = (await session.execute(
        select(InjectedAnomaly).where(InjectedAnomaly.shift_id.in_(shift_ids))
    )).scalars().all()
    events = (await session.execute(
        select(BehaviorEvent).where(BehaviorEvent.shift_id.in_(shift_ids))
    )).scalars().all()

    return (
        [Window(a.shift_id, a.injected_anomaly_type, a.event_start, a.event_end) for a in anomalies],
        [Window(e.shift_id, e.event_type, e.start, e.end) for e in events],
    )


def print_report(result: DetectionResult) -> None:
    total = result.total_injected
    rate = 100.0 * result.total_detected / total if total else 0.0
    print("\n" + "=" * 64)
    print("SAFE2GO behavior detection evaluation")
    print("=" * 64)
    print(f"Held-out split: shifts from {EVALUATION_SPLIT_START.date()}")
    print(f"Detected:                  {result.total_detected}/{total} ({rate:.1f}%)")
    print(f"False positives:           {result.total_false_positives}")
    print(f"Legit-wait shifts:         {result.legit_wait_shifts}, idling false positives: "
          f"{result.legit_wait_false_positives}")
    if result.explained:
        explained = ", ".join(f"{k} {v}" for k, v in sorted(result.explained.items()))
        print(f"Explained by injected idle windows (not counted): {explained}")
    print()
    print("Per type:")
    for kind in sorted(set(result.detected) | set(result.missed) | set(result.false_positives)):
        hit, miss, fp = result.detected[kind], result.missed[kind], result.false_positives[kind]
        n = hit + miss
        pct = f"{100.0 * hit / n:.0f}%" if n else "n/a"
        print(f"  {kind:<32} {hit}/{n} detected ({pct})  false positives: {fp}")
    print("=" * 64 + "\n")


async def main() -> None:
    setup_logging("WARNING")
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        anomalies, events = await load_windows(session)
    await engine.dispose()

    if not anomalies:
        log.warning("No held-out shifts or anomalies found. Run the batch generator first.")
        return
    print_report(evaluate(anomalies, events))


if __name__ == "__main__":
    asyncio.run(main())
