"""Historical aggregates for the ETA model.

Aggregates summarise past work per scope (operator, machine, skill level on
a machine type, fleet per machine type) and task type:

- avg_minutes_per_unit: total working minutes / total quantity
- avg_idle_ratio, avg_cycle_rate: from per-shift summaries

They feed the model as features and give the historical-average estimate
used for the admin breakdown and as the fallback when the model is not
available. Missing scopes fall back in a fixed order (cold start):

    operator pace:   operator -> skill -> fleet -> default
    machine pace:    machine -> fleet -> default

The same code serves evaluation (aggregates from training rows only, with
each training row left out of its own aggregates) and deployment
(aggregates from all history, stored in cloud.eta_aggregate).
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.analytics import EtaAggregate
from app.db.models.edge.assignment import EdgeEtaAggregate
from app.shared.enums import TaskType

SCOPE_OPERATOR = "operator"
SCOPE_MACHINE = "machine"
SCOPE_SKILL = "skill"
SCOPE_FLEET = "fleet"
SCOPE_DEFAULT = "default"

# A scope needs this many samples before its aggregate is trusted.
MIN_SAMPLES = 3

# Last-resort values when there is no history at all.
DEFAULT_MINUTES_PER_UNIT: dict[str, float] = {
    TaskType.EXCAVATION.value: 0.6,
    TaskType.MATERIAL_LOADING.value: 1.2,
    TaskType.TRENCHING.value: 0.7,
}
DEFAULT_IDLE_RATIO = 0.2
DEFAULT_CYCLE_RATE = 40.0
NO_AGGREGATES_VERSION = "none"


def skill_scope_id(machine_type: str, skill_level: str) -> str:
    return f"{machine_type}:{skill_level}"


@dataclass(frozen=True)
class TaskHistory:
    """One finished task: the unit of pace aggregation."""

    task_id: str
    shift_id: str
    operator_id: str
    machine_id: str
    machine_type: str
    skill_level: str
    task_type: str
    target_quantity: float
    actual_minutes: float


@dataclass(frozen=True)
class ShiftHistory:
    """One shift summary: the unit of idle-ratio and cycle-rate aggregation."""

    shift_id: str
    operator_id: str
    machine_id: str
    machine_type: str
    skill_level: str
    idle_ratio: float
    cycle_rate: float


@dataclass(frozen=True)
class AggregateRow:
    scope: str
    scope_id: str
    task_type: str
    avg_minutes_per_unit: float
    avg_idle_ratio: float
    avg_cycle_rate: float


@dataclass(frozen=True)
class HistoryFeatures:
    """History-derived inputs for one prediction."""

    operator_minutes_per_unit: float
    machine_minutes_per_unit: float
    operator_idle_ratio: float
    operator_cycle_rate: float
    operator_scope: str      # which scope supplied the operator pace
    machine_scope: str       # which scope supplied the machine pace

    def historical_average_minutes(self, quantity: float) -> float:
        """Historical-average estimate: this machine's past pace for the task type."""
        return quantity * self.machine_minutes_per_unit


RowResolver = Callable[[str, str, str], AggregateRow | None]


def resolve_history(
    resolve: RowResolver,
    operator_id: str,
    machine_id: str,
    machine_type: str,
    skill_level: str,
    task_type: str,
) -> HistoryFeatures:
    """Apply the cold-start fallback chain to find history for one task."""
    operator_chain = [
        (SCOPE_OPERATOR, operator_id),
        (SCOPE_SKILL, skill_scope_id(machine_type, skill_level)),
        (SCOPE_FLEET, machine_type),
    ]
    machine_chain = [(SCOPE_MACHINE, machine_id), (SCOPE_FLEET, machine_type)]

    op_scope, op_row = _first_row(resolve, operator_chain, task_type)
    mach_scope, mach_row = _first_row(resolve, machine_chain, task_type)
    default_pace = DEFAULT_MINUTES_PER_UNIT.get(task_type, max(DEFAULT_MINUTES_PER_UNIT.values()))

    return HistoryFeatures(
        operator_minutes_per_unit=op_row.avg_minutes_per_unit if op_row else default_pace,
        machine_minutes_per_unit=mach_row.avg_minutes_per_unit if mach_row else default_pace,
        operator_idle_ratio=op_row.avg_idle_ratio if op_row else DEFAULT_IDLE_RATIO,
        operator_cycle_rate=op_row.avg_cycle_rate if op_row else DEFAULT_CYCLE_RATE,
        operator_scope=op_scope,
        machine_scope=mach_scope,
    )


def _first_row(
    resolve: RowResolver,
    chain: list[tuple[str, str]],
    task_type: str,
) -> tuple[str, AggregateRow | None]:
    for scope, scope_id in chain:
        row = resolve(scope, scope_id, task_type)
        if row is not None:
            return scope, row
    return SCOPE_DEFAULT, None


# ---------------------------------------------------------------------------
# Computing aggregates from history
# ---------------------------------------------------------------------------


def _task_keys(t: TaskHistory) -> list[tuple[str, str, str]]:
    return [
        (SCOPE_OPERATOR, t.operator_id, t.task_type),
        (SCOPE_MACHINE, t.machine_id, t.task_type),
        (SCOPE_SKILL, skill_scope_id(t.machine_type, t.skill_level), t.task_type),
        (SCOPE_FLEET, t.machine_type, t.task_type),
    ]


def _shift_keys(s: ShiftHistory) -> list[tuple[str, str]]:
    return [
        (SCOPE_OPERATOR, s.operator_id),
        (SCOPE_MACHINE, s.machine_id),
        (SCOPE_SKILL, skill_scope_id(s.machine_type, s.skill_level)),
        (SCOPE_FLEET, s.machine_type),
    ]


class AggregateStats:
    """Running sums per scope, so a single row can be left out cheaply."""

    def __init__(self, tasks: Iterable[TaskHistory], shifts: Iterable[ShiftHistory]) -> None:
        # (scope, scope_id, task_type) -> [minutes, quantity, count]
        self._pace: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        # (scope, scope_id) -> [idle_ratio sum, cycle_rate sum, count]
        self._shift: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

        for t in tasks:
            for key in _task_keys(t):
                acc = self._pace[key]
                acc[0] += t.actual_minutes
                acc[1] += t.target_quantity
                acc[2] += 1
        for s in shifts:
            for key in _shift_keys(s):
                acc = self._shift[key]
                acc[0] += s.idle_ratio
                acc[1] += s.cycle_rate
                acc[2] += 1

    def resolver(
        self,
        exclude_task: TaskHistory | None = None,
        exclude_shift: ShiftHistory | None = None,
    ) -> RowResolver:
        """Return a resolver, optionally leaving one task and one shift out."""
        task_keys = set(_task_keys(exclude_task)) if exclude_task else set()
        shift_keys = set(_shift_keys(exclude_shift)) if exclude_shift else set()

        def resolve(scope: str, scope_id: str, task_type: str) -> AggregateRow | None:
            key = (scope, scope_id, task_type)
            if key not in self._pace:
                return None
            minutes, quantity, count = self._pace[key]
            if key in task_keys:
                minutes -= exclude_task.actual_minutes      # type: ignore[union-attr]
                quantity -= exclude_task.target_quantity    # type: ignore[union-attr]
                count -= 1
            if count < MIN_SAMPLES or quantity <= 0:
                return None
            idle, cycle = self._shift_means((scope, scope_id), shift_keys, exclude_shift)
            return AggregateRow(scope, scope_id, task_type, minutes / quantity, idle, cycle)

        return resolve

    def _shift_means(
        self,
        key: tuple[str, str],
        exclude_keys: set[tuple[str, str]],
        exclude_shift: ShiftHistory | None,
    ) -> tuple[float, float]:
        idle_sum, cycle_sum, count = self._shift.get(key, (0.0, 0.0, 0.0))
        if exclude_shift is not None and key in exclude_keys:
            idle_sum -= exclude_shift.idle_ratio
            cycle_sum -= exclude_shift.cycle_rate
            count -= 1
        if count <= 0:
            return DEFAULT_IDLE_RATIO, DEFAULT_CYCLE_RATE
        return idle_sum / count, cycle_sum / count

    def to_rows(self) -> list[AggregateRow]:
        """All trusted aggregates, sorted for a stable version hash."""
        resolve = self.resolver()
        rows = [resolve(*key) for key in sorted(self._pace)]
        return [r for r in rows if r is not None]


def aggregates_version(rows: list[AggregateRow]) -> str:
    """Deterministic version string derived from the aggregate contents."""
    digest = hashlib.sha256()
    for r in rows:
        digest.update(
            f"{r.scope}|{r.scope_id}|{r.task_type}|{r.avg_minutes_per_unit:.6f}|"
            f"{r.avg_idle_ratio:.6f}|{r.avg_cycle_rate:.6f};".encode()
        )
    return f"agg-{digest.hexdigest()[:12]}"


# ---------------------------------------------------------------------------
# Deployed aggregates
# ---------------------------------------------------------------------------


class AggregateTable:
    """Deployed aggregates, looked up by (scope, scope_id, task_type)."""

    def __init__(self, rows: Iterable[AggregateRow], version: str) -> None:
        self._rows = {(r.scope, r.scope_id, r.task_type): r for r in rows}
        self.version = version

    def __len__(self) -> int:
        return len(self._rows)

    def history(
        self,
        operator_id: str,
        machine_id: str,
        machine_type: str,
        skill_level: str,
        task_type: str,
    ) -> HistoryFeatures:
        return resolve_history(
            lambda scope, scope_id, tt: self._rows.get((scope, scope_id, tt)),
            operator_id,
            machine_id,
            machine_type,
            skill_level,
            task_type,
        )


async def load_aggregate_table(
    session: AsyncSession, source: type[EtaAggregate] | type[EdgeEtaAggregate]
) -> AggregateTable:
    """Read deployed aggregates: cloud.eta_aggregate for the cloud, the synced edge copy for the edge."""
    result = await session.execute(select(source))
    records = result.scalars().all()
    if not records:
        return AggregateTable([], NO_AGGREGATES_VERSION)
    rows = [
        AggregateRow(
            scope=r.scope,
            scope_id=r.scope_id or "",
            task_type=r.task_type,
            avg_minutes_per_unit=r.avg_minutes_per_unit,
            avg_idle_ratio=r.avg_idle_ratio,
            avg_cycle_rate=r.avg_cycle_rate,
        )
        for r in records
    ]
    return AggregateTable(rows, records[0].version)
