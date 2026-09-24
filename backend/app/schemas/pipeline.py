"""Schemas for the admin data pipeline page (edge spool, Kafka, cloud analytics)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MachineBacklogResponse(BaseModel):
    machine_id: str
    records: int
    oldest_event_time: datetime


class SpoolResponse(BaseModel):
    total: int
    by_kind: dict[str, int]
    by_machine: list[MachineBacklogResponse]
    # Real seconds since the oldest waiting record was spooled.
    oldest_age_seconds: float | None
    bytes: int
    limit: int
    # None while the backlog is not shrinking (link or broker down, or arrivals >= sends).
    drain_eta_seconds: float | None
    write_failures: int


class ForwarderResponse(BaseModel):
    state: str
    sent_total: int
    failed_total: int
    dropped_total: int
    gaps_total: int
    send_rate: float
    arrival_rate: float
    max_rate: float
    last_sent_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    retry_in_seconds: float | None


class ConsumerResponse(BaseModel):
    state: str
    received_total: int
    stored_total: int
    duplicates_total: int
    malformed_total: int
    lag: int | None
    last_batch_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None


class GapResponse(BaseModel):
    machine_id: str
    from_ts: datetime
    to_ts: datetime
    dropped_count: int
    dropped_at: datetime


class EventCountResponse(BaseModel):
    kind: str
    event_type: str
    count: int


class ArchiveResponse(BaseModel):
    ticks: int
    machines: int
    first_ts: datetime | None
    last_ts: datetime | None
    dropped_total: int
    recent_gaps: list[GapResponse]
    events_by_type: list[EventCountResponse]


class PipelineStatusResponse(BaseModel):
    enabled: bool
    bootstrap_servers: str
    topics: list[str]
    forwarder: ForwarderResponse
    spool: SpoolResponse
    consumer: ConsumerResponse
    archive: ArchiveResponse


class RollupPointResponse(BaseModel):
    bucket_start: datetime
    ticks: int
    engine_on_ticks: int
    working_ticks: int
    idle_ticks: int
    avg_rpm: float
    fuel_used: float
    load_cycles: int


class MachineRollupResponse(BaseModel):
    machine_id: str
    points: list[RollupPointResponse]
