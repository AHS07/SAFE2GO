"""Dataset loading and feature matrices for ETA training and evaluation.

Target: actual working time in minutes,
    actual_end - actual_start - paused_minutes - blocked_minutes,
for tasks that reached their target quantity. Tasks cut off by the shift
end are excluded because their true duration is unknown.

Split: time-ordered. The first TRAIN_DAYS of history train the model, the
rest is the test set. Never a random split.

Aggregates for evaluation come from training rows only. Each training row
is left out of its own aggregates so its target does not leak into its
features. Test rows use the training aggregates as they are.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import String, and_, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.analytics import ShiftSummary
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.shared.enums import SkillLevel, TaskStatus
from app.shared.eta_aggregates import (
    AggregateStats,
    HistoryFeatures,
    ShiftHistory,
    TaskHistory,
    resolve_history,
)
from app.shared.eta_model import EtaInput, feature_matrix, feature_row
from simulator.batch.shifts import TRAIN_DAYS


@dataclass(frozen=True)
class TaskSample:
    shift_date: datetime
    unit: str
    inp: EtaInput
    history: TaskHistory

    @property
    def actual_minutes(self) -> float:
        return self.history.actual_minutes


@dataclass(frozen=True)
class Dataset:
    tasks: list[TaskSample]
    shifts: list[ShiftHistory]
    shift_dates: dict[str, datetime]


@dataclass(frozen=True)
class Split:
    train: Dataset
    test: Dataset
    split_date: datetime


@dataclass(frozen=True)
class Matrix:
    x: np.ndarray
    y: np.ndarray
    samples: list[TaskSample]
    histories: list[HistoryFeatures]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def load_dataset(session: AsyncSession) -> Dataset:
    task_rows = await session.execute(
        select(Task, Shift, Machine)
        .join(Shift, Task.shift_id == Shift.shift_id)
        .join(Machine, Shift.machine_id == Machine.machine_id)
        .where(Task.status == TaskStatus.DONE.value)
        .where(Task.actual_start.is_not(None))
        .where(Task.actual_end.is_not(None))
        .where(Task.completed_quantity >= Task.target_quantity)
    )
    tasks: list[TaskSample] = []
    for task, shift, machine in task_rows.all():
        minutes = (
            (task.actual_end - task.actual_start).total_seconds() / 60.0
            - (task.paused_minutes or 0.0)
            - (task.blocked_minutes or 0.0)
        )
        if minutes <= 0:
            continue
        tasks.append(TaskSample(
            shift_date=shift.date,
            unit=task.quantity_unit,
            inp=EtaInput(
                task_type=task.task_type,
                material_type=task.material_type,
                target_quantity=task.target_quantity,
                skill_level=task.operator_skill_at_assignment,
                machine_type=machine.machine_type,
                machine_age=machine.machine_age,
                weather=shift.weather_forecast,
            ),
            history=TaskHistory(
                task_id=task.task_id,
                shift_id=shift.shift_id,
                operator_id=shift.operator_id,
                machine_id=shift.machine_id,
                machine_type=machine.machine_type,
                skill_level=task.operator_skill_at_assignment,
                task_type=task.task_type,
                target_quantity=task.target_quantity,
                actual_minutes=minutes,
            ),
        ))

    shift_rows = await session.execute(
        select(ShiftSummary, Shift, Machine, OperatorQualification.skill_level)
        .join(Shift, ShiftSummary.shift_id == Shift.shift_id)
        .join(Machine, Shift.machine_id == Machine.machine_id)
        .outerjoin(
            OperatorQualification,
            and_(
                OperatorQualification.operator_id == Shift.operator_id,
                # machine.machine_type is an enum, the qualification column is text.
                OperatorQualification.machine_type == cast(Machine.machine_type, String),
            ),
        )
    )
    shifts: list[ShiftHistory] = []
    shift_dates: dict[str, datetime] = {}
    for summary, shift, machine, skill in shift_rows.all():
        shift_dates[shift.shift_id] = shift.date
        shifts.append(ShiftHistory(
            shift_id=shift.shift_id,
            operator_id=shift.operator_id,
            machine_id=shift.machine_id,
            machine_type=machine.machine_type,
            skill_level=skill or SkillLevel.BEGINNER.value,
            idle_ratio=summary.idle_ratio,
            cycle_rate=summary.cycle_rate,
        ))

    tasks.sort(key=lambda t: (t.shift_date, t.history.task_id))
    shifts.sort(key=lambda s: (shift_dates[s.shift_id], s.shift_id))
    return Dataset(tasks, shifts, shift_dates)


def time_split(data: Dataset, train_days: int = TRAIN_DAYS) -> Split:
    """Time-ordered split: the first train_days of history train, the rest test."""
    if not data.tasks:
        raise ValueError("No finished tasks found. Run the batch generator first.")
    split_date = min(t.shift_date for t in data.tasks) + timedelta(days=train_days)

    def part(before: bool) -> Dataset:
        tasks = [t for t in data.tasks if (t.shift_date < split_date) == before]
        shifts = [s for s in data.shifts if (data.shift_dates[s.shift_id] < split_date) == before]
        return Dataset(tasks, shifts, data.shift_dates)

    return Split(train=part(True), test=part(False), split_date=split_date)


# ---------------------------------------------------------------------------
# Matrices
# ---------------------------------------------------------------------------


def training_matrix(train: Dataset) -> Matrix:
    """Features for training rows, each left out of its own aggregates."""
    stats = AggregateStats([t.history for t in train.tasks], train.shifts)
    shift_by_id = {s.shift_id: s for s in train.shifts}
    histories = [
        _history(stats.resolver(sample.history, shift_by_id.get(sample.history.shift_id)), sample)
        for sample in train.tasks
    ]
    return _matrix(train.tasks, histories)


def evaluation_matrix(train: Dataset, test: Dataset) -> Matrix:
    """Features for test rows, using aggregates from training rows only."""
    resolve = AggregateStats([t.history for t in train.tasks], train.shifts).resolver()
    histories = [_history(resolve, sample) for sample in test.tasks]
    return _matrix(test.tasks, histories)


def _history(resolve, sample: TaskSample) -> HistoryFeatures:
    h = sample.history
    return resolve_history(resolve, h.operator_id, h.machine_id, h.machine_type, h.skill_level, h.task_type)


def _matrix(samples: list[TaskSample], histories: list[HistoryFeatures]) -> Matrix:
    rows = [feature_row(s.inp, h) for s, h in zip(samples, histories, strict=True)]
    return Matrix(
        x=feature_matrix(rows),
        y=np.array([s.actual_minutes for s in samples], dtype=float),
        samples=samples,
        histories=histories,
    )
