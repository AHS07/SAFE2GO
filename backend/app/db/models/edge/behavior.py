"""Edge schema: Behavior events and training recommendations."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Enum, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import BehaviorEventType


class BehaviorEvent(Base):
    __tablename__ = "behavior_event"
    __table_args__ = {"schema": "edge"}

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(
        Enum(BehaviorEventType, schema="edge", name="behavioreventtype", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)


class Recommendation(Base):
    """Deduped training recommendation: at most one per module per operator per shift."""

    __tablename__ = "recommendation"
    __table_args__ = (
        UniqueConstraint("operator_id", "shift_id", "module_id", name="uq_recommendation_operator_shift_module"),
        {"schema": "edge"},
    )

    recommendation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False)
    module_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "incident" or "behavior"
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
