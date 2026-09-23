"""Service suggestion shown to operators and admins."""
from __future__ import annotations

from pydantic import BaseModel


class ServiceSuggestionResponse(BaseModel):
    status: str                     # ok, due_soon, overdue
    engine_hours: float
    hours_since_service: float
    hours_remaining: float
    service_interval_hours: float
    message: str | None
