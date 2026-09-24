"""Admin data pipeline routes: edge spool, Kafka forwarder and consumer, analytics.

In a real deployment the spool and forwarder figures come from each edge
gateway; in the demo both tiers run in one process, so they are read
directly. Available whether or not KAFKA_ENABLED is set, so the page can
explain that streaming is off.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.stream.analytics import archive_summary, recent_rollups
from app.config.settings import get_settings
from app.core.clock import wall_clock_now
from app.core.deps import require_admin
from app.db.session import get_db
from app.edge.stream import spool
from app.edge.stream.forwarder import drain_eta_seconds
from app.schemas.pipeline import (
    ArchiveResponse,
    ConsumerResponse,
    EventCountResponse,
    ForwarderResponse,
    GapResponse,
    MachineBacklogResponse,
    MachineRollupResponse,
    PipelineStatusResponse,
    RollupPointResponse,
    SpoolResponse,
)
from app.stream.runtime import consumer, forwarder

router = APIRouter(prefix="/api/admin/pipeline", tags=["pipeline"], dependencies=[Depends(require_admin)])

_ROLLUP_MINUTES_MAX = 240


@router.get("", response_model=PipelineStatusResponse)
async def get_pipeline(session: AsyncSession = Depends(get_db)) -> PipelineStatusResponse:
    settings = get_settings()
    backlog = await spool.backlog(session)
    archive = await archive_summary(session)
    fwd = forwarder.stats
    age = (
        round((wall_clock_now() - backlog.oldest_spooled_at).total_seconds(), 1)
        if backlog.oldest_spooled_at is not None
        else None
    )
    return PipelineStatusResponse(
        enabled=settings.kafka_enabled,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topics=[settings.kafka_topic_telemetry, settings.kafka_topic_events],
        forwarder=ForwarderResponse(
            state=fwd.state.value,
            sent_total=fwd.sent_total,
            failed_total=fwd.failed_total,
            dropped_total=fwd.dropped_total,
            gaps_total=fwd.gaps_total,
            send_rate=fwd.send_rate,
            arrival_rate=fwd.arrival_rate,
            max_rate=settings.stream_max_rate,
            last_sent_at=fwd.last_sent_at,
            last_error=fwd.last_error,
            last_error_at=fwd.last_error_at,
            retry_in_seconds=fwd.retry_in_seconds,
        ),
        spool=SpoolResponse(
            total=backlog.total,
            by_kind=backlog.by_kind,
            by_machine=[MachineBacklogResponse(**asdict(m)) for m in backlog.by_machine],
            oldest_age_seconds=age,
            bytes=backlog.spool_bytes,
            limit=settings.stream_spool_max_records,
            drain_eta_seconds=drain_eta_seconds(backlog.total, fwd),
            write_failures=spool.write_failures(),
        ),
        consumer=ConsumerResponse(**{**asdict(consumer.stats), "state": consumer.stats.state.value}),
        archive=ArchiveResponse(
            ticks=archive.ticks,
            machines=archive.machines,
            first_ts=archive.first_ts,
            last_ts=archive.last_ts,
            dropped_total=archive.dropped_total,
            recent_gaps=[GapResponse.model_validate(g, from_attributes=True) for g in archive.gaps],
            events_by_type=[EventCountResponse(kind=k, event_type=t, count=n) for k, t, n in archive.events_by_type],
        ),
    )


@router.get("/rollups", response_model=list[MachineRollupResponse])
async def get_rollups(
    minutes: int = Query(60, ge=1, le=_ROLLUP_MINUTES_MAX),
    session: AsyncSession = Depends(get_db),
) -> list[MachineRollupResponse]:
    """Per-machine, per-minute totals built from the Kafka archive."""
    by_machine: dict[str, list[RollupPointResponse]] = {}
    for row in await recent_rollups(session, minutes):
        by_machine.setdefault(row.machine_id, []).append(RollupPointResponse.model_validate(row, from_attributes=True))
    return [MachineRollupResponse(machine_id=m, points=p) for m, p in by_machine.items()]
