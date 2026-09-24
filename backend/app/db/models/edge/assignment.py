"""Edge schema: synced copies of master data, shifts, and tasks.

The cloud owns these records and delivers them through sync. The edge
reads only these copies. Once a task starts, the edge owns its status and
timing fields and reports them back to the cloud.

sync_seq is the outbox sequence of the last message applied to the row, so
an older message arriving late never overwrites newer data.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EdgeMachine(Base):
    __tablename__ = "machine"
    __table_args__ = {"schema": "edge"}

    machine_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_model: Mapped[str] = mapped_column(String(100), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(50), nullable=False)
    machine_age: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_status: Mapped[str] = mapped_column(String(20), nullable=False)
    service_interval_hours: Mapped[float] = mapped_column(Float, nullable=False)
    last_service_engine_hours: Mapped[float] = mapped_column(Float, nullable=False)
    engine_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bucket_capacity: Mapped[float] = mapped_column(Float, nullable=False)
    tilt_limit_degrees: Mapped[float] = mapped_column(Float, nullable=False)
    rated_max_rpm: Mapped[float] = mapped_column(Float, nullable=False)


class EdgeOperator(Base):
    """Operator roster. username and user_id support offline login; no password data."""

    __tablename__ = "operator"
    __table_args__ = {"schema": "edge"}

    operator_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operator_name: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)


class EdgeOperatorQualification(Base):
    __tablename__ = "operator_qualification"
    __table_args__ = {"schema": "edge"}

    operator_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_level: Mapped[str] = mapped_column(String(20), nullable=False)


class EdgeShift(Base):
    __tablename__ = "shift"
    __table_args__ = {"schema": "edge"}

    shift_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    date: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    weather_forecast: Mapped[str] = mapped_column(String(20), nullable=False)
    weather_actual: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sync_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class EdgeTask(Base):
    __tablename__ = "task"
    __table_args__ = {"schema": "edge"}

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    operator_skill_at_assignment: Mapped[str] = mapped_column(String(20), nullable=False)
    target_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(20), nullable=False)
    material_type: Mapped[str] = mapped_column(String(100), nullable=False)
    completed_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reassigned_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_start: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    blocked_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Start of the current pause or block; edge only, cleared on resume or unblock.
    hold_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_predicted_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    planning_eta: Mapped[float | None] = mapped_column(Float, nullable=True)
    revised_predicted_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    revised_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Why the ETA was last revised on the edge: "weather" or "pace".
    revision_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    aggregates_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sync_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class EdgeBehaviorBaseline(Base):
    __tablename__ = "behavior_baseline"
    __table_args__ = {"schema": "edge"}

    baseline_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    machine_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    median: Mapped[float] = mapped_column(Float, nullable=False)
    mad: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)


class EdgeEtaAggregate(Base):
    __tablename__ = "eta_aggregate"
    __table_args__ = {"schema": "edge"}

    aggregate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    avg_minutes_per_unit: Mapped[float] = mapped_column(Float, nullable=False)
    avg_idle_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cycle_rate: Mapped[float] = mapped_column(Float, nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)


class EdgeOfflineCredential(Base):
    """Shift-scoped offline credential. signature is the signed token holding the PIN verifier."""

    __tablename__ = "offline_credential"
    __table_args__ = {"schema": "edge"}

    credential_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    signature: Mapped[str] = mapped_column(String, nullable=False)
