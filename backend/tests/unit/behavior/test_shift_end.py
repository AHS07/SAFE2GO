"""End-of-shift idle ratio check in the live engine (I-11)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.edge.behavior.engine import BehaviorEngine

T0 = datetime(2030, 1, 7, 7, 0, tzinfo=UTC)


def _engine(engine_seconds: float, idle_seconds: float) -> tuple[BehaviorEngine, list]:
    engine = BehaviorEngine(
        machine_id="m-1",
        operator_id="op-1",
        shift_id="sh-1",
        rated_max_rpm=1800.0,
        machine_type="excavator",
        baseline_median=0.2,
        baseline_mad=0.05,
        shift_start=T0,
    )
    engine._total_engine_seconds = engine_seconds
    engine._total_idle_seconds = idle_seconds
    persisted: list = []

    async def _record(event, task_id, session) -> None:  # noqa: ANN001 - test double
        persisted.append(event)

    engine._persist_and_broadcast = _record  # type: ignore[method-assign]
    return engine, persisted


async def test_high_idle_ratio_detected_at_shift_end() -> None:
    engine, persisted = _engine(engine_seconds=3600, idle_seconds=2700)
    await engine.on_shift_end(T0 + timedelta(hours=1), session=None)
    assert [e.event_type for e in persisted] == ["high_idle_ratio"]
    assert engine.shift_ended


async def test_normal_idle_ratio_not_flagged() -> None:
    engine, persisted = _engine(engine_seconds=3600, idle_seconds=600)
    await engine.on_shift_end(T0 + timedelta(hours=1), session=None)
    assert persisted == []


async def test_shift_end_check_runs_once_per_shift() -> None:
    engine, persisted = _engine(engine_seconds=3600, idle_seconds=2700)
    await engine.on_shift_end(T0 + timedelta(hours=1), session=None)
    await engine.on_shift_end(T0 + timedelta(hours=2), session=None)
    assert len(persisted) == 1
