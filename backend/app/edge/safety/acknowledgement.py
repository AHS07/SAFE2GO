"""Incident acknowledgement rules (architecture.md section 4.2, prd.md F2.6, F2.7).

- Only the incident's own operator can acknowledge it.
- The machine must be stationary on its latest live tick. Unknown state
  (no live tick) is never treated as stationary.
- Acknowledging an active hazard does not resolve it: status becomes
  acknowledged and the alarm continues until the hazard clears.
- Acknowledging a hazard that has already cleared resolves the incident.
"""
from __future__ import annotations

from datetime import datetime

from app.core.errors import AckRequiresStationaryError, ForbiddenError
from app.db.models.edge.incident import Incident
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import IncidentStatus, Severity


def acknowledge(
    incident: Incident,
    tick: TelemetryTick | None,
    operator_id: str,
    now: datetime,
) -> None:
    if incident.operator_id != operator_id:
        raise ForbiddenError("This incident belongs to another operator.")
    if incident.status == IncidentStatus.RESOLVED.value:
        return
    if tick is None:
        raise AckRequiresStationaryError(
            "Machine state is unknown. Acknowledge once the machine reports it is stationary.",
            details={"reason": "no_telemetry"},
        )
    if tick.machine_speed > 0:
        raise AckRequiresStationaryError(
            "Stop the machine before acknowledging.",
            details={"machine_speed": tick.machine_speed},
        )

    if incident.acknowledged_at is None:
        incident.acknowledged_at = now
        incident.acknowledged_by = operator_id
    incident.status = (
        IncidentStatus.RESOLVED.value
        if incident.event_end is not None
        else IncidentStatus.ACKNOWLEDGED.value
    )


def status_after_hazard_clears(peak_severity: str, current_status: str) -> str:
    """WARNING resolves on clear. CRITICAL resolves only if already acknowledged."""
    if peak_severity == Severity.WARNING.value:
        return IncidentStatus.RESOLVED.value
    if current_status == IncidentStatus.ACKNOWLEDGED.value:
        return IncidentStatus.RESOLVED.value
    return current_status
