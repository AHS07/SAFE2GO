"""Cloud schema: Shift summary, behavior baselines, ETA aggregates, injected anomalies."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShiftSummary(Base):
    __tablename__ = "shift_summary"
    __table_args__ = {"schema": "cloud"}

    shift_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud.shift.shift_id", ondelete="CASCADE"), primary_key=True
    )
    engine_hours: Mapped[float] = mapped_column(Float, nullable=False)
    idle_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    idle_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    cycle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False)
    cycle_rate: Mapped[float] = mapped_column(Float, nullable=False)  # cycles per engine hour


class BehaviorBaseline(Base):
    __tablename__ = "behavior_baseline"
    __table_args__ = {"schema": "cloud"}

    baseline_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String(20), nullable=False)       # "operator" or "fleet"
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # operator_id or None for fleet
    machine_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)      # e.g. "idle_ratio"
    median: Mapped[float] = mapped_column(Float, nullable=False)
    mad: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)


class EtaAggregate(Base):
    __tablename__ = "eta_aggregate"
    __table_args__ = {"schema": "cloud"}

    aggregate_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String(20), nullable=False)   # "operator", "machine", "skill", "fleet"
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    avg_minutes_per_unit: Mapped[float] = mapped_column(Float, nullable=False)
    avg_idle_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cycle_rate: Mapped[float] = mapped_column(Float, nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)


class InjectedAnomaly(Base):
    """Ground truth for evaluation only. Never read by the detection engine."""

    __tablename__ = "injected_anomaly"
    __table_args__ = {"schema": "cloud"}

    anomaly_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.shift.shift_id"), nullable=False)
    injected_anomaly_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    event_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncConflict(Base):
    __tablename__ = "sync_conflict"
    __table_args__ = {"schema": "cloud"}

    conflict_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.task.task_id"), nullable=False)
    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False)
    edge_actual_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    cloud_reassigned_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False)
