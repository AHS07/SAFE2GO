"""Tune the high idle ratio threshold on the training split.

Threshold = baseline median + multiplier x MAD. For each candidate
multiplier the detector runs over the training days (the first TRAIN_DAYS)
and is scored against the injected high_idle_ratio anomalies there. The
held-out days are not used for the choice; they are scored once, for the
current and the recommended value, so the effect is reported honestly.

Choice: among multipliers up to MAX_MULTIPLIER that still detect every
training anomaly, the one with the fewest training false positives (the
smallest such value on a tie). The cap keeps the cut-off near a conventional
robust outlier limit: the injected anomalies are strong, so the training
data alone would favour a cut-off that misses milder real cases.

    python -m evaluation.tune_idle_ratio
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.db.models.cloud.analytics import InjectedAnomaly
from app.shared.enums import AnomalyType, BehaviorEventType
from evaluation.detection_report import Window, evaluate
from simulator.batch.edge_replay import ShiftIdleRecord, idle_ratio_events
from simulator.batch.idle_records import load_idle_records

CANDIDATES = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0]
MAX_MULTIPLIER = 6.0    # about 4 scaled MADs
# Normal-consistent MAD scale, shown so the result can be read as "k scaled MADs".
MAD_SCALE = 1.4826
KIND = BehaviorEventType.HIGH_IDLE_RATIO.value


@dataclass(frozen=True)
class Score:
    multiplier: float
    detected: int
    injected: int
    false_positives: int


def score(records: list[ShiftIdleRecord], anomalies: list[Window], multiplier: float, training: bool) -> Score:
    shift_ids = {r.shift_id for r in records if r.in_baseline_window == training}
    events = idle_ratio_events(records, mad_multiplier=multiplier)
    windows = [Window(sid, e.event_type, e.start, e.end) for sid, e in events.items() if sid in shift_ids]
    relevant = [a for a in anomalies if a.shift_id in shift_ids and a.kind in (KIND, AnomalyType.LEGIT_WAIT.value)]
    result = evaluate(relevant, windows)
    return Score(
        multiplier=multiplier,
        detected=result.detected[KIND],
        injected=result.detected[KIND] + result.missed[KIND],
        false_positives=result.false_positives[KIND],
    )


def choose(scores: list[Score]) -> Score:
    complete = [s for s in scores if s.detected == s.injected and s.multiplier <= MAX_MULTIPLIER]
    pool = complete or scores
    return min(pool, key=lambda s: (s.false_positives, -s.detected, s.multiplier))


async def load(session: AsyncSession) -> tuple[list[ShiftIdleRecord], list[Window]]:
    records = await load_idle_records(session)
    anomalies = (await session.execute(select(InjectedAnomaly))).scalars().all()
    return records, [Window(a.shift_id, a.injected_anomaly_type, a.event_start, a.event_end) for a in anomalies]


def _line(s: Score) -> str:
    return (f"  {s.multiplier:>5.1f} x MAD ({s.multiplier / MAD_SCALE:4.2f} x scaled MAD)   "
            f"detected {s.detected}/{s.injected}   false positives {s.false_positives}")


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        records, anomalies = await load(session)
    await engine.dispose()

    current = get_thresholds().behavior.idle_ratio_mad_multiplier
    training = [score(records, anomalies, m, training=True) for m in sorted({*CANDIDATES, current})]
    best = choose(training)

    print("High idle ratio threshold, training split")
    for s in training:
        marks = (" current" if s.multiplier == current else "") + (" recommended" if s is best else "")
        print(_line(s) + marks)
    print()
    print("Held-out split (reported once, not used for the choice)")
    for label, m in (("current", current), ("recommended", best.multiplier)):
        s = score(records, anomalies, m, training=False)
        print(f"  {label:<12}" + _line(s)[2:])


if __name__ == "__main__":
    asyncio.run(main())
