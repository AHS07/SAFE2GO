"""Behavior analytics engine.

One BehaviorEngine instance per machine per shift. Processes telemetry
ticks and incident notifications, persists behavior_event rows, and
pushes coaching cards over WebSocket.

Design rules:
- Never reads injected_anomalies.
- Uses cached baselines from the edge schema — never calls the cloud.
- Each detector is isolated: a failure in one never stops the others.
- All timestamps come from tick.timestamp, never from datetime.now().
"""
from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.db.models.edge.behavior import BehaviorEvent
from app.edge.behavior.coaching import coaching_message
from app.edge.behavior.detectors.excessive_idling import (
    DetectionEvent,
    ExcessiveIdlingDetector,
)
from app.edge.behavior.detectors.high_rpm_travel import HighRpmTravelDetector
from app.edge.behavior.detectors.idle_ratio import IdleRatioDetector
from app.edge.behavior.detectors.repeated_overloading import RepeatedOverloadingDetector
from app.edge.behavior.detectors.repeated_seatbelt import RepeatedSeatbeltDetector
from app.edge.outbox import queue_behavior_event
from app.edge.training.recommendations import RecommendationOutcome, recommend_for_source
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import IncidentType

log = logging.getLogger("safe2go.behavior_engine")


class BehaviorEngine:
    """Per-machine, per-shift behavior analytics engine.

    Parameters
    ----------
    machine_id:       machine being monitored
    operator_id:      operator for this shift
    shift_id:         current shift
    rated_max_rpm:    from machine master data
    machine_type:     determines high-rpm travel speed threshold
    baseline_median:  cached idle_ratio median for this operator/fleet
    baseline_mad:     cached idle_ratio MAD
    shift_start:      simulation-clock shift start timestamp
    """

    def __init__(
        self,
        machine_id: str,
        operator_id: str,
        shift_id: str,
        rated_max_rpm: float,
        machine_type: str,
        baseline_median: float,
        baseline_mad: float,
        shift_start: datetime,
    ) -> None:
        self._machine_id = machine_id
        self._operator_id = operator_id
        self._shift_id = shift_id
        self._shift_start = shift_start
        self._baseline_median = baseline_median
        self._baseline_mad = baseline_mad

        # Tick-driven detectors
        self._idling = ExcessiveIdlingDetector()
        self._high_rpm = HighRpmTravelDetector(rated_max_rpm, machine_type)

        # Incident-driven detectors
        self._overloading = RepeatedOverloadingDetector()
        self._seatbelt = RepeatedSeatbeltDetector()

        # Shift-end detector
        self._idle_ratio_detector = IdleRatioDetector()

        # Tracking for idle ratio calculation
        self._total_engine_seconds: float = 0.0
        self._total_idle_seconds: float = 0.0
        self._last_tick_at: datetime | None = None
        self._shift_ended = False

        # WebSocket messages for this tick, sent only after it commits.
        self._effects: list[Callable[[], Awaitable[None]]] = []

        # WS broadcast callable (optional)
        self._ws_broadcast: Any | None = None

    def set_ws_broadcast(self, fn: Any) -> None:
        self._ws_broadcast = fn

    # ------------------------------------------------------------------
    # Transaction support (see SafetyEngine.checkpoint)
    # ------------------------------------------------------------------

    _STATE = (
        "_idling", "_high_rpm", "_overloading", "_seatbelt",
        "_total_engine_seconds", "_total_idle_seconds", "_last_tick_at", "_shift_ended",
    )

    def checkpoint(self) -> dict:
        return copy.deepcopy({name: getattr(self, name) for name in self._STATE})

    def restore(self, saved: dict) -> None:
        for name, value in saved.items():
            setattr(self, name, value)
        self._effects.clear()

    async def flush_effects(self) -> None:
        effects, self._effects = self._effects, []
        for effect in effects:
            await effect()

    def _tick_seconds(self, ts: datetime) -> float:
        """Time this tick stands for: the gap since the previous tick, capped so a
        pause in the stream is not counted as engine time. The first tick counts
        as one nominal tick."""
        nominal = float(get_settings().sim_tick_seconds)
        previous, self._last_tick_at = self._last_tick_at, ts
        if previous is None:
            return nominal
        gap = (ts - previous).total_seconds()
        return min(max(gap, 0.0), get_thresholds().behavior.max_tick_gap_seconds)

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    async def process_tick(
        self,
        tick: TelemetryTick,
        task_status: str | None,
        session: AsyncSession,
    ) -> None:
        """Process one telemetry tick."""
        # Update idle ratio accumulators with the time this tick covers.
        seconds = self._tick_seconds(tick.timestamp)
        if tick.engine_running:
            self._total_engine_seconds += seconds
            is_idle = (
                not tick.hydraulic_active
                and tick.machine_speed == 0.0
            )
            if is_idle:
                self._total_idle_seconds += seconds

        # Run tick-driven detectors with isolation
        for name, fn in [
            ("excessive_idling", lambda: self._idling.update(
                tick.timestamp,
                tick.engine_running,
                tick.hydraulic_active,
                tick.machine_speed,
                task_status,
            )),
            ("high_rpm_travel", lambda: self._high_rpm.update(
                tick.timestamp,
                tick.engine_rpm,
                tick.machine_speed,
            )),
        ]:
            try:
                event = fn()
                if event:
                    await self._persist_and_broadcast(event, tick.task_id, session)
            except Exception as exc:
                log.error(
                    "Behavior detector error",
                    extra={"detector": name, "machine_id": self._machine_id, "error": str(exc)},
                    exc_info=exc,
                )

    # ------------------------------------------------------------------
    # Incident notifications
    # ------------------------------------------------------------------

    async def notify_incident(
        self,
        incident_type: str,
        event_ts: datetime,
        session: AsyncSession,
    ) -> None:
        """Called by the safety engine when an incident opens."""
        event: DetectionEvent | None = None

        try:
            if incident_type == IncidentType.OVERLOADING.value:
                event = self._overloading.record_incident(event_ts)
            elif incident_type == IncidentType.SEATBELT.value:
                event = self._seatbelt.record_incident(event_ts)
        except Exception as exc:
            log.error(
                "Behavior incident notification error",
                extra={"incident_type": incident_type, "error": str(exc)},
                exc_info=exc,
            )
            return

        if event:
            await self._persist_and_broadcast(event, None, session)

    # ------------------------------------------------------------------
    # Shift end
    # ------------------------------------------------------------------

    @property
    def shift_id(self) -> str:
        return self._shift_id

    @property
    def shift_ended(self) -> bool:
        return self._shift_ended

    async def on_shift_end(
        self,
        shift_end: datetime,
        session: AsyncSession,
    ) -> bool:
        """Run end-of-shift detectors (idle ratio). Runs at most once per shift.

        Returns True when this call ended the shift, False if it had already ended.
        """
        if self._shift_ended:
            return False
        self._shift_ended = True
        if self._total_engine_seconds == 0:
            return True

        idle_ratio = self._total_idle_seconds / self._total_engine_seconds
        try:
            event = self._idle_ratio_detector.check(
                shift_start=self._shift_start,
                shift_end=shift_end,
                idle_ratio=idle_ratio,
                baseline_median=self._baseline_median,
                baseline_mad=self._baseline_mad,
            )
            if event:
                await self._persist_and_broadcast(event, None, session)
        except Exception as exc:
            log.error(
                "Idle ratio detector error",
                extra={"machine_id": self._machine_id, "error": str(exc)},
                exc_info=exc,
            )
        return True

    # ------------------------------------------------------------------
    # Persistence and broadcast
    # ------------------------------------------------------------------

    async def _persist_and_broadcast(
        self,
        event: DetectionEvent,
        task_id: str | None,
        session: AsyncSession,
    ) -> None:
        row = BehaviorEvent(
            event_id=str(uuid.uuid4()),
            machine_id=self._machine_id,
            operator_id=self._operator_id,
            task_id=task_id,
            shift_id=self._shift_id,
            event_type=event.event_type,
            start=event.start,
            end=event.end,
            magnitude=event.magnitude,
            baseline_value=event.baseline_value,
        )
        session.add(row)
        await session.flush()
        await queue_behavior_event(session, row)

        log.info(
            "Behavior event detected",
            extra={
                "event_type": event.event_type,
                "machine_id": self._machine_id,
                "magnitude": event.magnitude,
            },
        )

        recommendation = await self._recommend(row, session)
        message = {
            "type": "coaching",
            "data": {
                "event_id": row.event_id,
                "event_type": event.event_type,
                "message": coaching_message(event.event_type, event.magnitude),
                "magnitude": event.magnitude,
                "start": event.start.isoformat(),
                "recommendation": (
                    {"module_id": recommendation.module_id, "module_title": recommendation.module_title}
                    if recommendation
                    else None
                ),
            },
        }
        self._effects.append(lambda: self._send(message))

    async def _send(self, message: dict) -> None:
        if not self._ws_broadcast:
            return
        try:
            await self._ws_broadcast(self._machine_id, message)
        except Exception as exc:
            log.warning("Coaching broadcast failed", extra={"machine_id": self._machine_id, "error": str(exc)})

    async def _recommend(
        self, row: BehaviorEvent, session: AsyncSession
    ) -> RecommendationOutcome | None:
        # Savepoint: a recommendation failure never loses the behavior event.
        try:
            async with session.begin_nested():
                return await recommend_for_source(
                    session,
                    operator_id=self._operator_id,
                    shift_id=self._shift_id,
                    source_type="behavior",
                    type_value=row.event_type,
                    source_id=row.event_id,
                    created_at=row.end,
                )
        except Exception as exc:
            log.error(
                "Training recommendation failed",
                extra={"event_type": row.event_type, "machine_id": self._machine_id, "error": str(exc)},
                exc_info=exc,
            )
            return None

    def reset(self) -> None:
        self._idling.reset()
        self._high_rpm.reset()
        self._overloading.reset()
        self._seatbelt.reset()
        self._total_engine_seconds = 0.0
        self._total_idle_seconds = 0.0
        self._last_tick_at = None
        self._effects.clear()


# ---------------------------------------------------------------------------
# Registry: one engine per (machine_id, shift_id)
# ---------------------------------------------------------------------------

_engines: dict[str, BehaviorEngine] = {}


def get_engine(machine_id: str) -> BehaviorEngine | None:
    return _engines.get(machine_id)


def register_engine(engine: BehaviorEngine) -> None:
    _engines[engine._machine_id] = engine


def deregister_engine(machine_id: str) -> None:
    _engines.pop(machine_id, None)
