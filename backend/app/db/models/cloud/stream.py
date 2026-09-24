"""Cloud schema: analytics storage fed from Kafka.

Every table is keyed by the edge event_id (or by machine and minute for the
rollup), so a record delivered twice is stored once.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelemetryArchive(Base):
    """Raw live ticks, for long-term history and fleet analytics."""

    __tablename__ = "telemetry_archive"
    __table_args__ = {"schema": "cloud"}

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    edge_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    engine_running: Mapped[bool] = mapped_column(Boolean, nullable=False)
    engine_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    hydraulic_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    machine_speed: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False)
    load_cycles: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_pct: Mapped[float] = mapped_column(Float, nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class TelemetryGap(Base):
    """Raw ticks dropped from a full edge spool. Never silent: each drop is reported here."""

    __tablename__ = "telemetry_gap"
    __table_args__ = {"schema": "cloud"}

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    from_ts: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    to_ts: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    dropped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dropped_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class StreamEvent(Base):
    """Incidents and behavior events, as streamed for fleet analytics.

    The sync outbox stays the source of truth for incidents; this is a copy.
    """

    __tablename__ = "stream_event"
    __table_args__ = {"schema": "cloud"}

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class FleetMinuteRollup(Base):
    """Per-machine, per-minute totals. Recomputed from the archive, never incremented."""

    __tablename__ = "fleet_minute_rollup"
    __table_args__ = {"schema": "cloud"}

    machine_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bucket_start: Mapped[object] = mapped_column(DateTime(timezone=True), primary_key=True)
    ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_on_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    working_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False)
    load_cycles: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
