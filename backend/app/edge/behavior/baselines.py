"""Idle-ratio baseline lookup for the live behavior engine.

Reads the edge copy of the baselines, delivered by sync. Operator baseline when the operator has enough shifts,
otherwise the fleet baseline for the machine type.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import get_thresholds
from app.db.models.edge.assignment import EdgeBehaviorBaseline

log = logging.getLogger("safe2go.behavior_baselines")

_METRIC = "idle_ratio"
# With no baseline at all the threshold becomes 1.0, which an idle ratio
# can never exceed. No history means no basis to flag the operator.
NO_BASELINE = (1.0, 0.0)


async def idle_ratio_baseline(
    session: AsyncSession, operator_id: str, machine_type: str
) -> tuple[float, float]:
    """Return (median, mad) for the operator on this machine type."""
    min_shifts = get_thresholds().behavior.baseline_min_shifts
    rows = (
        await session.execute(
            select(EdgeBehaviorBaseline)
            .where(EdgeBehaviorBaseline.metric == _METRIC)
            .where(EdgeBehaviorBaseline.machine_type == machine_type)
        )
    ).scalars().all()

    for row in rows:
        if row.scope == "operator" and row.scope_id == operator_id and row.sample_count >= min_shifts:
            return row.median, row.mad
    for row in rows:
        if row.scope == "fleet":
            return row.median, row.mad

    log.warning(
        "No idle ratio baseline, high idle ratio check disabled",
        extra={"operator_id": operator_id, "machine_type": machine_type},
    )
    return NO_BASELINE
