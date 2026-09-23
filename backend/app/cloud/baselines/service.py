"""Baseline computation service (cloud tier).

Reads shift_summaries from the cloud schema and computes per-operator
and fleet-wide baselines using median and MAD (median absolute deviation).

Never uses mean or standard deviation — baselines must be robust to
the injected anomalies that sit inside the training history.

Called:
  - After batch generation (to pre-compute baselines).
  - Periodically in production to refresh as new shifts complete.

Results are written to cloud.behavior_baseline and copied directly to the
edge schema cache (Phase 8 replaces the direct copy with sync messages).
"""
from __future__ import annotations

import logging
import uuid

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.analytics import BehaviorBaseline, ShiftSummary
from app.db.models.cloud.shift import Shift

log = logging.getLogger("safe2go.baselines")

_BASELINE_VERSION = "v1"
_METRIC = "idle_ratio"
_MIN_SAMPLES = 2   # need at least 2 to compute MAD meaningfully


def _mad(values: np.ndarray) -> float:
    """Median absolute deviation."""
    return float(np.median(np.abs(values - np.median(values))))


async def compute_and_store_baselines(session: AsyncSession) -> int:
    """Compute all baselines and write them to cloud.behavior_baseline.

    Returns the number of baseline rows written.
    """
    # Load all shift summaries joined with shift (for operator_id and machine_type)
    result = await session.execute(
        select(ShiftSummary, Shift)
        .join(Shift, ShiftSummary.shift_id == Shift.shift_id)
    )
    rows = result.all()

    if not rows:
        log.warning("No shift summaries found — skipping baseline computation")
        return 0

    # Build lookup: operator_id -> list of idle_ratios (per machine_type)
    from collections import defaultdict
    op_machine_ratios: dict[tuple[str, str], list[float]] = defaultdict(list)
    machine_ratios: dict[str, list[float]] = defaultdict(list)   # machine_type -> ratios

    # We need machine_type — join through machine
    from app.db.models.cloud.machine import Machine
    machines_result = await session.execute(select(Machine))
    machine_type_map = {m.machine_id: m.machine_type for m in machines_result.scalars().all()}

    for summary, shift in rows:
        mt = machine_type_map.get(shift.machine_id)
        if mt is None:
            continue
        op_machine_ratios[(shift.operator_id, mt)].append(summary.idle_ratio)
        machine_ratios[mt].append(summary.idle_ratio)

    # Delete old baselines
    await session.execute(text("DELETE FROM cloud.behavior_baseline"))

    written = 0

    # Per-operator baselines
    for (operator_id, machine_type), ratios in op_machine_ratios.items():
        if len(ratios) < _MIN_SAMPLES:
            continue
        arr = np.array(ratios)
        session.add(BehaviorBaseline(
            baseline_id=str(uuid.uuid4()),
            scope="operator",
            scope_id=operator_id,
            machine_type=machine_type,
            metric=_METRIC,
            median=float(np.median(arr)),
            mad=_mad(arr),
            sample_count=len(ratios),
            version=_BASELINE_VERSION,
        ))
        written += 1

    # Fleet-wide baselines per machine type
    for machine_type, ratios in machine_ratios.items():
        if len(ratios) < _MIN_SAMPLES:
            continue
        arr = np.array(ratios)
        session.add(BehaviorBaseline(
            baseline_id=str(uuid.uuid4()),
            scope="fleet",
            scope_id=None,
            machine_type=machine_type,
            metric=_METRIC,
            median=float(np.median(arr)),
            mad=_mad(arr),
            sample_count=len(ratios),
            version=_BASELINE_VERSION,
        ))
        written += 1

    await session.flush()
    log.info("Baselines computed", extra={"rows_written": written})
    return written


async def get_baseline_for_operator(
    operator_id: str,
    machine_type: str,
    session: AsyncSession,
    min_shifts: int = 10,
) -> tuple[float, float]:
    """Return (median, mad) for an operator, falling back to fleet.

    Falls back to fleet baseline if the operator has fewer than min_shifts.
    Returns (0.3, 0.05) as a hard fallback if no baseline exists at all.
    """
    # Check operator sample count
    op_result = await session.execute(
        select(BehaviorBaseline)
        .where(BehaviorBaseline.scope == "operator")
        .where(BehaviorBaseline.scope_id == operator_id)
        .where(BehaviorBaseline.machine_type == machine_type)
        .where(BehaviorBaseline.metric == _METRIC)
        .limit(1)
    )
    op_baseline = op_result.scalar_one_or_none()

    if op_baseline and op_baseline.sample_count >= min_shifts:
        return op_baseline.median, op_baseline.mad

    # Fall back to fleet
    fleet_result = await session.execute(
        select(BehaviorBaseline)
        .where(BehaviorBaseline.scope == "fleet")
        .where(BehaviorBaseline.machine_type == machine_type)
        .where(BehaviorBaseline.metric == _METRIC)
        .limit(1)
    )
    fleet = fleet_result.scalar_one_or_none()
    if fleet:
        return fleet.median, fleet.mad

    # Hard fallback — no history at all
    log.warning(
        "No baseline found, using defaults",
        extra={"operator_id": operator_id, "machine_type": machine_type},
    )
    return 0.3, 0.05
