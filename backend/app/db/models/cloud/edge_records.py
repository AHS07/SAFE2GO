"""Cloud schema: incidents and behavior events received from the edge through sync.

Plain strings instead of enums: these rows mirror what the edge reported.
sync_seq is the edge outbox sequence of the last applied message.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CloudIncident(Base):
    __tablename__ = "incident"
    __table_args__ = {"schema": "cloud"}

    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    event_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(30), nullable=False)
    peak_severity: Mapped[str] = mapped_column(String(20), nullable=False)
    escalation_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sync_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class CloudBehaviorEvent(Base):
    __tablename__ = "behavior_event"
    __table_args__ = {"schema": "cloud"}

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
