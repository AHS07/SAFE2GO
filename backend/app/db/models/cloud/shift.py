"""Cloud schema: Shift."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import WeatherCategory


class Shift(Base):
    __tablename__ = "shift"
    __table_args__ = {"schema": "cloud"}

    shift_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    machine_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.machine.machine_id"), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.operator.operator_id"), nullable=False)
    date: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    weather_forecast: Mapped[str] = mapped_column(
        Enum(WeatherCategory, schema="cloud", name="weathercategory", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # Unknown until the shift has run; the live stream falls back to the forecast.
    weather_actual: Mapped[str | None] = mapped_column(String(50), nullable=True)
