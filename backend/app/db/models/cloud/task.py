"""Cloud schema: Task (assignment owned by cloud, status owned by edge once started)."""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import QuantityUnit, SkillLevel, TaskStatus, TaskType


class Task(Base):
    __tablename__ = "task"
    __table_args__ = {"schema": "cloud"}

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.shift.shift_id"), nullable=False)

    task_type: Mapped[str] = mapped_column(
        Enum(TaskType, schema="cloud", name="tasktype", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    operator_skill_at_assignment: Mapped[str] = mapped_column(
        Enum(SkillLevel, schema="cloud", name="skilllevel", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    target_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    quantity_unit: Mapped[str] = mapped_column(
        Enum(QuantityUnit, schema="cloud", name="quantityunit", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    material_type: Mapped[str] = mapped_column(String(100), nullable=False)
    completed_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(
        Enum(TaskStatus, schema="cloud", name="taskstatus", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TaskStatus.ASSIGNED,
    )

    scheduled_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    # scheduled_end = scheduled_start + planning_eta (derived, stored for validation)
    scheduled_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reassigned_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actual_start: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    blocked_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ETA fields
    raw_predicted_time: Mapped[float | None] = mapped_column(Float, nullable=True)   # minutes
    planning_eta: Mapped[float | None] = mapped_column(Float, nullable=True)          # minutes
    revised_predicted_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    revised_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    aggregates_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Edge outbox sequence of the last task_status applied (out-of-order guard).
    edge_sync_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
