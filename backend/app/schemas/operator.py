"""Pydantic schemas for operator-facing API requests and responses."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ShiftResponse(BaseModel):
    shift_id: str
    machine_id: str
    operator_id: str
    operator_name: str
    scheduled_start: datetime
    scheduled_end: datetime
    weather_forecast: str
    machine_model: str
    machine_type: str
    bucket_capacity: float
    tilt_limit_degrees: float
    rated_max_rpm: float


class TaskResponse(BaseModel):
    task_id: str
    shift_id: str
    task_type: str
    material_type: str
    target_quantity: float
    quantity_unit: str
    completed_quantity: float
    status: str
    scheduled_start: datetime
    scheduled_end: datetime | None
    # One ETA for the operator: planning_eta, or revised + buffer once revised.
    eta_minutes: float | None
    eta_from_history: bool                 # True when the model was unavailable
    actual_start: datetime | None
    actual_end: datetime | None


class TaskActionRequest(BaseModel):
    notes: str | None = None


class IncidentResponse(BaseModel):
    incident_id: str
    incident_type: str
    peak_severity: str
    status: str
    source: str
    event_start: datetime
    event_end: datetime | None
    escalation_reason: str | None
    description: str | None
    acknowledged_at: datetime | None


class ManualIncidentRequest(BaseModel):
    description: str = Field(min_length=3, max_length=500)


class MachineStatusResponse(BaseModel):
    """Latest live sensor state for the operator's machine.

    live is False when no tick has arrived since the simulator started;
    every sensor field is then null, meaning unknown, never safe.
    """

    machine_id: str
    live: bool
    parked: bool
    timestamp: datetime | None = None
    engine_running: bool | None = None
    engine_rpm: float | None = None
    engine_hours: float | None = None
    fuel_used: float | None = None
    hydraulic_active: bool | None = None
    machine_speed: float | None = None
    payload_pct: float | None = None
    seatbelt_status: str | None = None
    seat_occupied: bool | None = None
    park_brake: bool | None = None
    gear_state: str | None = None
    proximity_distance: float | None = None
    ambient_temp: float | None = None
    visibility: float | None = None
    tilt_angle: float | None = None
    task_id: str | None = None


class CoachingResponse(BaseModel):
    event_id: str
    event_type: str
    message: str
    magnitude: float
    baseline_value: float | None
    start: datetime
    end: datetime
    module_id: str | None
    module_title: str | None


class LoginRequest(BaseModel):
    username: str
    password: str


class OfflineLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    pin: str = Field(min_length=4, max_length=12, pattern=r"^[0-9]+$")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: str
    operator_id: str | None = None
    offline: bool = False       # signed in at the edge with a shift credential
