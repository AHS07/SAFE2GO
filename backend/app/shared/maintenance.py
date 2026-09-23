"""Service suggestion from engine hours (PRD F11).

Rule based, no failure prediction: the share of the service interval used
since the last service. Used by the edge (live meter) and the cloud (synced
meter), so both show the same status.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config.thresholds import MaintenanceThresholds


class ServiceStatus(StrEnum):
    OK = "ok"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"


@dataclass(frozen=True)
class ServiceSuggestion:
    status: ServiceStatus
    hours_since_service: float
    hours_remaining: float
    service_interval_hours: float
    engine_hours: float
    message: str | None


def service_suggestion(
    engine_hours: float,
    last_service_engine_hours: float,
    service_interval_hours: float,
    cfg: MaintenanceThresholds,
) -> ServiceSuggestion:
    since = max(0.0, engine_hours - last_service_engine_hours)
    remaining = service_interval_hours - since
    used = since / service_interval_hours if service_interval_hours > 0 else 0.0

    if used >= cfg.overdue_fraction:
        status = ServiceStatus.OVERDUE
        message = f"Service overdue by {abs(remaining):.0f} engine hours. Plan a service before the next shift."
    elif used >= cfg.due_soon_fraction:
        status = ServiceStatus.DUE_SOON
        message = f"Service due in {remaining:.0f} engine hours. Book it with maintenance."
    else:
        status = ServiceStatus.OK
        message = None

    return ServiceSuggestion(
        status=status,
        hours_since_service=round(since, 1),
        hours_remaining=round(remaining, 1),
        service_interval_hours=service_interval_hours,
        engine_hours=round(engine_hours, 1),
        message=message,
    )
