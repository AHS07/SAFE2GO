"""Edge schema: Safety incidents."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import EscalationReason, IncidentStatus, IncidentType, Severity


class Incident(Base):
    __tablename__ = "incident"
    __table_args__ = {"schema": "edge"}

    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    event_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    event_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    incident_type: Mapped[str] = mapped_column(
        Enum(IncidentType, schema="edge", name="incidenttype", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    peak_severity: Mapped[str] = mapped_column(
        Enum(Severity, schema="edge", name="severity", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    escalation_reason: Mapped[str | None] = mapped_column(
        Enum(EscalationReason, schema="edge", name="escalationreason", values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(IncidentStatus, schema="edge", name="incidentstatus", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=IncidentStatus.OPEN,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="engine")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    acknowledged_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
