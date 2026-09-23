"""Cloud schema: Training modules, quiz results, anomaly-training map."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrainingModule(Base):
    __tablename__ = "training_module"
    __table_args__ = {"schema": "cloud"}

    module_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(50), nullable=False)   # e.g. "text", "video"
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    content_path: Mapped[str | None] = mapped_column(String(300), nullable=True)


class QuizResult(Base):
    __tablename__ = "quiz_result"
    __table_args__ = {"schema": "cloud"}

    result_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.operator.operator_id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.training_module.module_id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AnomalyTrainingMap(Base):
    __tablename__ = "anomaly_training_map"
    __table_args__ = {"schema": "cloud"}

    map_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # "incident" or "behavior"
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # matches incident_type or behavior_event.event_type
    type_value: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_module_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud.training_module.module_id"), nullable=False
    )
