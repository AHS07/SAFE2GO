"""Shift summary (PRD F10), the same shape for the operator (edge) and the admin (cloud)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReportTask(BaseModel):
    task_id: str
    task_type: str
    status: str
    target_quantity: float
    completed_quantity: float
    quantity_unit: str
    working_minutes: float | None       # actual_time, once the task is done


class ReportTime(BaseModel):
    engine_hours: float
    working_minutes: float
    idle_minutes: float
    idle_ratio: float
    cycle_count: int
    fuel_used: float


class ReportCount(BaseModel):
    type: str
    count: int


class ReportTraining(BaseModel):
    module_id: str
    title: str


class ShiftReportResponse(BaseModel):
    shift_id: str
    operator_name: str
    machine_model: str
    scheduled_start: datetime
    scheduled_end: datetime
    source: str                         # "edge" (live) or "cloud" (synced)
    tasks_done: int
    tasks_total: int
    tasks: list[ReportTask]
    time: ReportTime | None             # None until telemetry exists (edge) or the summary has synced (cloud)
    incidents_total: int
    incidents_critical: int
    incidents: list[ReportCount]
    behavior: list[ReportCount]
    training: list[ReportTraining]
