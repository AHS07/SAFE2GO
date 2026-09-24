"""Edge schema: Per-tick telemetry (raw sensor state only)."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = {"schema": "edge"}

    telemetry_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # task_id is nullable — null between tasks
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # Engine state
    engine_running: Mapped[bool] = mapped_column(Boolean, nullable=False)
    engine_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    engine_hours: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False)  # litres cumulative

    # Hydraulic / motion
    hydraulic_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    machine_speed: Mapped[float] = mapped_column(Float, nullable=False)  # km/h

    # Load / payload
    # load_cycles: per-tick count (not cumulative — usually 0 or 1)
    load_cycles: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_pct: Mapped[float] = mapped_column(Float, nullable=False)   # instantaneous % of rated capacity
    # cycle_payload_pct: set only on cycle-completion ticks, null otherwise
    cycle_payload_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Safety state
    seatbelt_status: Mapped[str] = mapped_column(String(20), nullable=False)  # "fastened" / "unfastened"
    seat_occupied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    park_brake: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gear_state: Mapped[str] = mapped_column(String(20), nullable=False)   # "neutral", "forward", "reverse", "park"
    # proximity_distance: null means no hazard detected in sensor range
    proximity_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # False: the proximity reading is unknown (sensor fault), never treated as clear.
    proximity_sensor_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Working conditions
    ambient_temp: Mapped[float] = mapped_column(Float, nullable=False)    # Celsius
    visibility: Mapped[float] = mapped_column(Float, nullable=False)       # metres
    tilt_angle: Mapped[float] = mapped_column(Float, nullable=False)       # degrees
