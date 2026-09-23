"""Cloud schema: Machine master data."""
from __future__ import annotations

import uuid

from sqlalchemy import Enum, Float, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import MachineStatus, MachineType


class Machine(Base):
    __tablename__ = "machine"
    __table_args__ = {"schema": "cloud"}

    machine_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    machine_model: Mapped[str] = mapped_column(String(100), nullable=False)
    machine_type: Mapped[str] = mapped_column(
        Enum(MachineType, schema="cloud", name="machinetype", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    machine_age: Mapped[int] = mapped_column(Integer, nullable=False)  # years
    machine_status: Mapped[str] = mapped_column(
        Enum(MachineStatus, schema="cloud", name="machinestatus", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        server_default=text("'available'"),
    )
    service_interval_hours: Mapped[float] = mapped_column(Float, nullable=False)
    last_service_engine_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Current hour meter: last service reading plus hours run since (shift summaries).
    engine_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bucket_capacity: Mapped[float] = mapped_column(Float, nullable=False)  # m3
    tilt_limit_degrees: Mapped[float] = mapped_column(Float, nullable=False)
    rated_max_rpm: Mapped[float] = mapped_column(Float, nullable=False)
