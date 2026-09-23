"""Pydantic schemas for telemetry ticks and ingest responses."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TelemetryTick(BaseModel):
    """Raw per-tick sensor state. No derived alert fields — alerts are engine outputs."""

    timestamp: datetime
    machine_id: str
    operator_id: str
    task_id: str | None = None
    shift_id: str

    # Engine
    engine_running: bool
    engine_rpm: float = Field(ge=0)
    engine_hours: float = Field(ge=0)
    fuel_used: float = Field(ge=0)

    # Motion
    hydraulic_active: bool
    machine_speed: float = Field(ge=0)   # km/h

    # Load
    load_cycles: int = Field(ge=0)
    payload_pct: float = Field(ge=0)
    cycle_payload_pct: float | None = Field(default=None, ge=0)

    # Safety state
    seatbelt_status: str        # "fastened" | "unfastened"
    seat_occupied: bool
    park_brake: bool
    gear_state: str             # "neutral" | "forward" | "reverse" | "park"
    proximity_distance: float | None = Field(default=None, ge=0)

    # Working conditions
    ambient_temp: float
    visibility: float = Field(ge=0)
    tilt_angle: float = Field(ge=0)

    @field_validator("seatbelt_status")
    @classmethod
    def validate_seatbelt(cls, v: str) -> str:
        if v not in ("fastened", "unfastened"):
            raise ValueError(f"Invalid seatbelt_status: {v!r}")
        return v

    @field_validator("gear_state")
    @classmethod
    def validate_gear(cls, v: str) -> str:
        if v not in ("neutral", "forward", "reverse", "park"):
            raise ValueError(f"Invalid gear_state: {v!r}")
        return v


class TelemetryIngestResponse(BaseModel):
    accepted: int
    rejected: int
