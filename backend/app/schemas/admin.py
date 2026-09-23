"""Pydantic schemas for admin-facing API responses."""
from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.maintenance import ServiceSuggestionResponse
from app.shared.enums import MaterialType, QuantityUnit, TaskType, WeatherCategory


class UnusualFeatureResponse(BaseModel):
    feature: str
    value: str
    description: str


class EtaBreakdownResponse(BaseModel):
    task_id: str
    historical_average_minutes: float
    raw_predicted_time: float | None
    buffer_minutes: float
    planning_eta: float | None
    revised_predicted_time: float | None
    revised_at: datetime | None
    operator_eta: float | None
    is_fallback: bool
    model_version: str | None
    aggregates_version: str | None
    current_model_version: str
    unusual_features: list[UnusualFeatureResponse]


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------


class QualificationResponse(BaseModel):
    machine_type: str
    skill_level: str


class OperatorAdminResponse(BaseModel):
    operator_id: str
    operator_name: str
    qualifications: list[QualificationResponse]


class MachineAdminResponse(BaseModel):
    machine_id: str
    machine_model: str
    machine_type: str
    machine_status: str
    machine_age: int
    bucket_capacity: float
    service: ServiceSuggestionResponse


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


class ShiftCreateRequest(BaseModel):
    operator_id: str
    machine_id: str
    scheduled_start: AwareDatetime
    scheduled_end: AwareDatetime
    weather_forecast: WeatherCategory


class ShiftAdminResponse(BaseModel):
    shift_id: str
    operator_id: str
    machine_id: str
    scheduled_start: datetime
    scheduled_end: datetime
    weather_forecast: str
    weather_actual: str | None
    # "queued" while a message for it waits for the cloud link, else "delivered".
    delivery: str = "delivered"


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    shift_id: str
    task_type: TaskType
    target_quantity: float = Field(gt=0, le=100_000)
    quantity_unit: QuantityUnit
    material_type: MaterialType
    # Omitted: right after the last planned task in the shift, or at shift start.
    scheduled_start: AwareDatetime | None = None


class TaskReassignRequest(BaseModel):
    target_shift_id: str
    scheduled_start: AwareDatetime | None = None


class TaskAdminResponse(BaseModel):
    task_id: str
    shift_id: str
    task_type: str
    material_type: str
    target_quantity: float
    quantity_unit: str
    completed_quantity: float
    status: str
    operator_skill_at_assignment: str
    scheduled_start: datetime
    scheduled_end: datetime | None
    reassigned_at: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    raw_predicted_time: float | None
    planning_eta: float | None
    revised_predicted_time: float | None
    is_fallback: bool
    model_version: str | None
    aggregates_version: str | None
    delivery: str = "delivered"


class AssignmentWarningResponse(BaseModel):
    code: str
    message: str
    details: dict


class AssignmentResponse(BaseModel):
    task: TaskAdminResponse
    warnings: list[AssignmentWarningResponse]


class ConflictResponse(BaseModel):
    conflict_id: str
    task_id: str
    conflict_type: str
    edge_actual_start: datetime
    cloud_changed_at: datetime
    created_at: datetime
    resolved: bool
