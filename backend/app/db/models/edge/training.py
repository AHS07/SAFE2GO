"""Edge schema: cached training content, recommendation map, and quiz results.

The cloud owns modules and the map; the edge keeps a copy so training
works while the cloud is unreachable. Quiz results are written on the
edge and flow back to the cloud through sync (phase 8).
"""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EdgeTrainingModule(Base):
    __tablename__ = "training_module"
    __table_args__ = {"schema": "edge"}

    module_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    content_path: Mapped[str | None] = mapped_column(String(300), nullable=True)


class EdgeAnomalyTrainingMap(Base):
    __tablename__ = "anomaly_training_map"
    __table_args__ = (
        UniqueConstraint("source_type", "type_value", name="uq_edge_training_map_source"),
        {"schema": "edge"},
    )

    map_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    type_value: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_module_id: Mapped[str] = mapped_column(String(36), nullable=False)


class EdgeQuizResult(Base):
    __tablename__ = "quiz_result"
    __table_args__ = {"schema": "edge"}

    result_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    module_id: Mapped[str] = mapped_column(String(36), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
