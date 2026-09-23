"""Safety engine orchestrator.

One SafetyEngine instance per machine. Called on every telemetry tick.

Responsibilities:
- Runs all 7 rules against the tick.
- Isolates failures: a crashing rule marks itself degraded, others keep running.
- Opens, escalates, and closes incidents in the edge DB.
- Notifies the clock driver of incident open/close for auto-slow.
- Broadcasts incident updates via the WebSocket manager.
- Applies repeat escalation: if the tracker says a new WARNING should be
  CRITICAL, the incident opens at CRITICAL instead.

Rules map to incident types:
  seatbelt         -> IncidentType.SEATBELT
  operator_seated  -> IncidentType.OPERATOR_NOT_SEATED
  proximity        -> IncidentType.PROXIMITY
  tilt             -> IncidentType.TILT
  overload         -> IncidentType.OVERLOADING
  visibility       -> IncidentType.WORKING_CONDITION
  temperature      -> IncidentType.WORKING_CONDITION  (same type, visibility sub-type)

Architecture rule: this module never calls datetime.now() — all timestamps
come from the simulation clock via the tick's timestamp field.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.incident import Incident
from app.edge.outbox import queue_incident
from app.edge.safety.acknowledgement import status_after_hazard_clears
from app.edge.safety.escalation import RepeatEscalationTracker

# Rule modules
from app.edge.safety.rules import (
    operator_seated,
    overload,
    proximity,
    seatbelt,
    temperature,
    tilt,
    visibility,
)
from app.edge.safety.state_machine import RuleStateMachine
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import EscalationReason, IncidentStatus, IncidentType, Severity

log = logging.getLogger("safe2go.safety_engine")

# Called after an incident opens: (incident_type, incident_id, tick, session).
IncidentListener = Callable[[str, str, TelemetryTick, AsyncSession], Awaitable[None]]


@dataclass
class RuleSlot:
    """Container for one rule's state machine, module, and runtime state."""
    name: str
    incident_type: str
    machine: RuleStateMachine
    module: Any                         # the rule module (has evaluate())
    degraded: bool = False
    open_incident_id: str | None = None
    extra_kwargs: dict = field(default_factory=dict)


class SafetyEngine:
    """Per-machine safety engine instance.

    Create one instance per machine at application startup and pass it
    telemetry ticks as they arrive.

    Parameters
    ----------
    machine_id:
        The machine this engine manages.
    tilt_limit_degrees:
        Machine-specific tilt limit loaded from master data.
    """

    def __init__(
        self,
        machine_id: str,
        tilt_limit_degrees: float,
    ) -> None:
        self._machine_id = machine_id
        self._tilt_limit = tilt_limit_degrees
        self._escalation = RepeatEscalationTracker()
        self._open_incident_count = 0

        self._rules: list[RuleSlot] = [
            RuleSlot(
                name="seatbelt",
                incident_type=IncidentType.SEATBELT.value,
                machine=seatbelt.build_state_machine(),
                module=seatbelt,
            ),
            RuleSlot(
                name="operator_seated",
                incident_type=IncidentType.OPERATOR_NOT_SEATED.value,
                machine=operator_seated.build_state_machine(),
                module=operator_seated,
            ),
            RuleSlot(
                name="proximity",
                incident_type=IncidentType.PROXIMITY.value,
                machine=proximity.build_state_machine(),
                module=proximity,
            ),
            RuleSlot(
                name="tilt",
                incident_type=IncidentType.TILT.value,
                machine=tilt.build_state_machine(),
                module=tilt,
                extra_kwargs={"tilt_limit_degrees": tilt_limit_degrees},
            ),
            RuleSlot(
                name="overload",
                incident_type=IncidentType.OVERLOADING.value,
                machine=overload.build_state_machine(),
                module=overload,
            ),
            RuleSlot(
                name="visibility",
                incident_type=IncidentType.WORKING_CONDITION.value,
                machine=visibility.build_state_machine(),
                module=visibility,
            ),
            RuleSlot(
                name="temperature",
                incident_type=IncidentType.WORKING_CONDITION.value,
                machine=temperature.build_state_machine(),
                module=temperature,
            ),
        ]

        # Clock driver reference (optional) for auto-slow notification.
        self._clock_driver: Any | None = None
        # WebSocket broadcast callable (optional).
        self._ws_broadcast: Any | None = None
        self._incident_listeners: list[IncidentListener] = []

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_clock_driver(self, driver: Any) -> None:
        self._clock_driver = driver

    def set_ws_broadcast(self, broadcast_fn: Any) -> None:
        self._ws_broadcast = broadcast_fn

    def add_incident_listener(self, listener: IncidentListener) -> None:
        self._incident_listeners.append(listener)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_tick(
        self,
        tick: TelemetryTick,
        session: AsyncSession,
    ) -> None:
        """Process one telemetry tick. Called by the ingest service."""
        sim_ts = tick.timestamp.timestamp()

        for slot in self._rules:
            if slot.degraded:
                continue
            try:
                await self._evaluate_rule(slot, tick, sim_ts, session)
            except Exception as exc:
                slot.degraded = True
                log.error(
                    "Safety rule degraded",
                    extra={
                        "rule": slot.name,
                        "machine_id": self._machine_id,
                        "error": str(exc),
                    },
                    exc_info=exc,
                )
                await self._broadcast_degraded(slot.name)

    # ------------------------------------------------------------------
    # Per-rule evaluation
    # ------------------------------------------------------------------

    async def _evaluate_rule(
        self,
        slot: RuleSlot,
        tick: TelemetryTick,
        sim_ts: float,
        session: AsyncSession,
    ) -> None:
        result = slot.module.evaluate(slot.machine, tick, sim_ts, **slot.extra_kwargs)

        if result.opened:
            severity = slot.machine.current_severity

            # Check repeat escalation before writing the incident.
            repeat_escalate = self._escalation.record_and_check(
                self._machine_id, slot.name, sim_ts, severity
            )
            if repeat_escalate:
                severity = Severity.CRITICAL.value
                slot.machine._current_severity = severity

            incident_id = await self._open_incident(
                slot, tick, severity, result.escalation_reason, session
            )
            slot.open_incident_id = incident_id
            self._open_incident_count += 1
            if self._clock_driver:
                self._clock_driver.notify_incident_opened()
            await self._broadcast_incident(slot, incident_id, "opened", severity)
            await self._notify_incident_listeners(slot.incident_type, incident_id, tick, session)

        elif result.escalate_to_critical and slot.open_incident_id:
            await self._escalate_incident(
                slot.open_incident_id, result.escalation_reason, session
            )
            await self._broadcast_incident(slot, slot.open_incident_id, "escalated", "critical")

        elif result.closed and slot.open_incident_id:
            closed_id = slot.open_incident_id
            await self._close_incident(closed_id, tick.timestamp, session)
            slot.open_incident_id = None
            self._open_incident_count = max(0, self._open_incident_count - 1)
            if self._clock_driver:
                self._clock_driver.notify_incident_closed()
            await self._broadcast_incident(slot, closed_id, "closed", slot.machine.current_severity)

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    async def _open_incident(
        self,
        slot: RuleSlot,
        tick: TelemetryTick,
        severity: str,
        escalation_reason: str | None,
        session: AsyncSession,
    ) -> str:
        incident_id = str(uuid.uuid4())
        incident = Incident(
            incident_id=incident_id,
            shift_id=tick.shift_id,
            machine_id=self._machine_id,
            operator_id=tick.operator_id,
            task_id=tick.task_id,
            event_start=tick.timestamp,
            incident_type=slot.incident_type,
            peak_severity=severity,
            escalation_reason=_map_escalation_reason(escalation_reason),
            status=IncidentStatus.OPEN.value,
            source="engine",
        )
        session.add(incident)
        await session.flush()
        queue_incident(session, incident)
        log.info(
            "Incident opened",
            extra={
                "incident_id": incident_id,
                "rule": slot.name,
                "severity": severity,
                "machine_id": self._machine_id,
            },
        )
        return incident_id

    async def _escalate_incident(
        self,
        incident_id: str,
        escalation_reason: str | None,
        session: AsyncSession,
    ) -> None:
        incident = await session.get(Incident, incident_id)
        if incident is None:
            return
        incident.peak_severity = Severity.CRITICAL.value
        incident.escalation_reason = _map_escalation_reason(escalation_reason)
        await session.flush()
        queue_incident(session, incident)
        log.info(
            "Incident escalated to CRITICAL",
            extra={"incident_id": incident_id, "machine_id": self._machine_id},
        )

    async def _close_incident(
        self,
        incident_id: str,
        event_end: datetime,
        session: AsyncSession,
    ) -> None:
        incident = await session.get(Incident, incident_id)
        if incident is None:
            return

        # WARNING incidents auto-resolve. CRITICAL incidents resolve only
        # once acknowledged; otherwise they stay open with event_end set.
        incident.event_end = event_end
        incident.status = status_after_hazard_clears(incident.peak_severity, incident.status)
        await session.flush()
        queue_incident(session, incident)
        log.info(
            "Incident closed",
            extra={
                "incident_id": incident_id,
                "new_status": incident.status,
                "machine_id": self._machine_id,
            },
        )

    async def _notify_incident_listeners(
        self,
        incident_type: str,
        incident_id: str,
        tick: TelemetryTick,
        session: AsyncSession,
    ) -> None:
        # Each listener runs in a savepoint so its failure never rolls back
        # the incident or stops the safety engine.
        for listener in self._incident_listeners:
            try:
                async with session.begin_nested():
                    await listener(incident_type, incident_id, tick, session)
            except Exception as exc:
                log.error(
                    "Incident listener failed",
                    extra={"incident_type": incident_type, "machine_id": self._machine_id, "error": str(exc)},
                    exc_info=exc,
                )

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast_incident(
        self, slot: RuleSlot, incident_id: str | None, event: str, severity: str
    ) -> None:
        if self._ws_broadcast is None:
            return
        try:
            await self._ws_broadcast(
                self._machine_id,
                {
                    "type": "incident",
                    "data": {
                        "incident_id": incident_id,
                        "rule": slot.name,
                        "incident_type": slot.incident_type,
                        "severity": severity,
                        "event": event,
                    },
                },
            )
        except Exception as exc:
            # A broadcast failure never stops safety processing.
            log.warning("Incident broadcast failed", extra={"machine_id": self._machine_id, "error": str(exc)})

    async def _broadcast_degraded(self, rule_name: str) -> None:
        if self._ws_broadcast is None:
            return
        try:
            await self._ws_broadcast(
                self._machine_id,
                {
                    "type": "safety_degraded",
                    "data": {"rule": rule_name, "machine_id": self._machine_id},
                },
            )
        except Exception as exc:
            log.warning("Degraded-rule broadcast failed", extra={"machine_id": self._machine_id, "error": str(exc)})

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all rule state machines (called on simulator reset)."""
        for slot in self._rules:
            slot.machine.reset()
            slot.open_incident_id = None
            slot.degraded = False
        self._escalation.reset_machine(self._machine_id)
        self._open_incident_count = 0


# ---------------------------------------------------------------------------
# Registry: one engine per machine
# ---------------------------------------------------------------------------

_engines: dict[str, SafetyEngine] = {}


def get_engine(machine_id: str) -> SafetyEngine | None:
    return _engines.get(machine_id)


def register_engine(engine: SafetyEngine) -> None:
    _engines[engine._machine_id] = engine


def deregister_engine(machine_id: str) -> None:
    _engines.pop(machine_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_escalation_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    mapping = {
        "grace_period": EscalationReason.GRACE_PERIOD.value,
        "threshold": EscalationReason.THRESHOLD.value,
        "repeat": EscalationReason.REPEAT.value,
    }
    return mapping.get(reason, reason)
