"""Pure assembly of the shift summary from rows either tier provides."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.schemas.shift_report import ReportCount, ReportTask, ReportTime
from app.shared.enums import Severity, TaskStatus

_MINUTES_PER_HOUR = 60.0


def actual_minutes(task: Any) -> float | None:
    """actual_end - actual_start - paused - blocked, for a finished task (PRD F1)."""
    if task.status != TaskStatus.DONE.value or task.actual_start is None or task.actual_end is None:
        return None
    span = (task.actual_end - task.actual_start).total_seconds() / 60.0
    return round(max(0.0, span - (task.paused_minutes or 0.0) - (task.blocked_minutes or 0.0)), 1)


def report_tasks(tasks: Iterable[Any]) -> list[ReportTask]:
    return [
        ReportTask(
            task_id=t.task_id,
            task_type=t.task_type,
            status=t.status,
            target_quantity=t.target_quantity,
            completed_quantity=round(t.completed_quantity or 0.0, 1),
            quantity_unit=t.quantity_unit,
            working_minutes=actual_minutes(t),
        )
        for t in tasks
        if t.status != TaskStatus.CANCELLED.value
    ]


def report_time(summary: dict | None) -> ReportTime | None:
    """Working time is engine time that was not idle. summary has the shift_summary fields."""
    if summary is None:
        return None
    engine_minutes = summary["engine_hours"] * _MINUTES_PER_HOUR
    return ReportTime(
        engine_hours=round(summary["engine_hours"], 2),
        working_minutes=round(max(0.0, engine_minutes - summary["idle_minutes"]), 1),
        idle_minutes=round(summary["idle_minutes"], 1),
        idle_ratio=round(summary["idle_ratio"], 3),
        cycle_count=int(summary["cycle_count"]),
        fuel_used=round(summary["fuel_used"], 1),
    )


def counts(values: Iterable[str]) -> list[ReportCount]:
    return [ReportCount(type=k, count=v) for k, v in Counter(values).most_common()]


def critical_count(incidents: Iterable[Any]) -> int:
    return sum(1 for i in incidents if i.peak_severity == Severity.CRITICAL.value)
