"""Recompute stored high idle ratio events for the generated history.

Run after changing behavior.idle_ratio_mad_multiplier so the detection
report reflects the new threshold without regenerating the data. Uses only
stored summaries and baselines from the training window, never the injected
ground truth.

    python -m scripts.replay_idle_ratio
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.core.logging import setup_logging
from app.db.models.edge.behavior import BehaviorEvent
from app.shared.enums import BehaviorEventType
from simulator.batch.edge_replay import idle_ratio_events
from simulator.batch.idle_records import load_idle_records

log = logging.getLogger("safe2go.replay_idle_ratio")


async def run() -> None:
    setup_logging("INFO")
    engine = create_async_engine(get_settings().database_url)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        records = await load_idle_records(session)
        by_id = {r.shift_id: r for r in records}
        await session.execute(
            delete(BehaviorEvent)
            .where(BehaviorEvent.event_type == BehaviorEventType.HIGH_IDLE_RATIO.value)
            .where(BehaviorEvent.shift_id.in_(list(by_id)))
        )
        machine_of = await _machines(session, list(by_id))
        events = idle_ratio_events(records)
        for shift_id, event in events.items():
            session.add(BehaviorEvent(
                event_id=str(uuid.uuid4()),
                machine_id=machine_of[shift_id],
                operator_id=by_id[shift_id].operator_id,
                task_id=None,
                shift_id=shift_id,
                event_type=event.event_type,
                start=event.start,
                end=event.end,
                magnitude=event.magnitude,
                baseline_value=event.baseline_value,
            ))
        await session.commit()
    await engine.dispose()
    log.info(
        "High idle ratio events recomputed",
        extra={"events": len(events), "mad_multiplier": get_thresholds().behavior.idle_ratio_mad_multiplier},
    )


async def _machines(session: AsyncSession, shift_ids: list[str]) -> dict[str, str]:
    from sqlalchemy import select

    from app.db.models.cloud.shift import Shift

    rows = (await session.execute(select(Shift.shift_id, Shift.machine_id).where(Shift.shift_id.in_(shift_ids)))).all()
    return dict(rows)


if __name__ == "__main__":
    asyncio.run(run())
