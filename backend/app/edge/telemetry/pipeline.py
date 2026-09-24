"""Live edge pipeline: one call per telemetry tick.

For each tick: persist it, run the safety engine and the behavior engine,
add task progress on cycle-completion ticks, record training
recommendations for new incidents, commit, and push updates to the
operator's WebSocket channel.

Engines are created per machine and rebuilt when the machine's shift
changes. The end-of-shift check (idle ratio) runs once per shift: when the
machine moves to a new shift, or when end_shift is called (demo control).

A tick is one transaction. The engines are checkpointed before it and
restored if it rolls back, so their in-memory state never runs ahead of the
database; WebSocket messages and auto-slow changes are sent only after the
commit. A failed tick is logged and the next tick is processed normally.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.edge.assignment import EdgeMachine, EdgeShift, EdgeTask
from app.edge.behavior import engine as behavior_registry
from app.edge.behavior.baselines import idle_ratio_baseline
from app.edge.behavior.engine import BehaviorEngine
from app.edge.outbox import queue_shift_summary
from app.edge.safety import engine as safety_registry
from app.edge.safety.engine import SafetyEngine
from app.edge.shift_summary import build_shift_summary
from app.edge.tasks.service import record_cycle
from app.edge.telemetry import state
from app.edge.telemetry.ingest import ingest_tick
from app.edge.training.recommendations import recommend_for_source
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import TaskStatus
from app.shared.eta_model import displayed_eta

log = logging.getLogger("safe2go.edge_pipeline")

Broadcast = Callable[[str, dict], Awaitable[None]]


@dataclass
class MachineContext:
    shift_id: str
    bucket_capacity: float
    safety: SafetyEngine
    behavior: BehaviorEngine


class EdgePipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        broadcast: Broadcast,
        clock_driver: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._broadcast = broadcast
        self._clock_driver = clock_driver
        self._contexts: dict[str, MachineContext] = {}
        # Pipeline-level WebSocket messages for the current tick (sent after commit).
        self._effects: list[dict] = []

    async def process(self, tick: TelemetryTick) -> None:
        progress: dict | None = None
        self._effects = []
        saved: list[tuple[Any, dict]] = []
        async with self._session_factory() as session:
            try:
                ctx, previous = await self._context_for(tick, session)
                saved = [(engine, engine.checkpoint()) for engine in _engines(ctx, previous)]
                if previous is not None:
                    # The machine moved on to a new shift: close out the previous one.
                    await _close_shift(previous, tick.timestamp, session)
                task = await session.get(EdgeTask, tick.task_id) if tick.task_id else None
                await ingest_tick(
                    tick,
                    session,
                    safety_engine=ctx.safety,
                    behavior_engine=ctx.behavior,
                    task_status=task.status if task else None,
                )
                if task is not None and _completes_cycle(tick, task):
                    # One call per cycle reported in this tick; cycle_payload_pct is their average.
                    for _ in range(tick.load_cycles):
                        await record_cycle(task, tick.cycle_payload_pct, ctx.bucket_capacity, session)
                    progress = _progress_message(task)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                for engine, checkpoint in saved:
                    engine.restore(checkpoint)
                self._effects = []
                log.error(
                    "Tick processing failed",
                    extra={"machine_id": tick.machine_id, "error": str(exc)},
                    exc_info=exc,
                )
                return

        if self._contexts.get(tick.machine_id) is not ctx:
            if previous is not None:
                ctx.safety.adopt_unresolved(previous.safety.release_unresolved())
            self._install(tick.machine_id, ctx)
        for engine in _engines(previous, ctx):
            await engine.flush_effects()
        for message in self._effects:
            await self._safe_broadcast(tick.machine_id, message)
        state.record_tick(tick)
        await self._safe_broadcast(tick.machine_id, {"type": "telemetry", "data": tick.model_dump(mode="json")})
        if progress is not None:
            await self._safe_broadcast(tick.machine_id, {"type": "progress", "data": progress})

    async def end_shift(self, machine_id: str, at: datetime) -> bool:
        """Run the end-of-shift check for the machine's current shift.

        Returns False when the machine has no engines yet (no live tick).
        """
        ctx = self._contexts.get(machine_id)
        if ctx is None:
            return False
        saved = ctx.behavior.checkpoint()
        async with self._session_factory() as session:
            try:
                await _close_shift(ctx, at, session)
                await session.commit()
            except Exception:
                await session.rollback()
                # Not marked ended, so the presenter can run it again.
                ctx.behavior.restore(saved)
                raise
        await ctx.behavior.flush_effects()
        log.info("Shift end check run", extra={"machine_id": machine_id, "shift_id": ctx.shift_id})
        return True

    def reset(self) -> None:
        """Drop all engines and live machine state (called when streaming stops)."""
        for machine_id in list(self._contexts):
            safety_registry.deregister_engine(machine_id)
            behavior_registry.deregister_engine(machine_id)
        self._contexts.clear()
        state.clear()

    # ------------------------------------------------------------------
    # Engine setup
    # ------------------------------------------------------------------

    async def _context_for(
        self, tick: TelemetryTick, session: AsyncSession
    ) -> tuple[MachineContext, MachineContext | None]:
        """The engines for this tick's shift, and the previous shift's engines when
        the machine has just changed shift. A new context is installed only after
        the tick commits (see process)."""
        current = self._contexts.get(tick.machine_id)
        if current is not None and current.shift_id == tick.shift_id:
            return current, None

        machine = await session.get(EdgeMachine, tick.machine_id)
        shift = await session.get(EdgeShift, tick.shift_id)
        if machine is None or shift is None:
            raise LookupError(f"Unknown machine or shift for tick: {tick.machine_id} / {tick.shift_id}")

        median, mad = await idle_ratio_baseline(session, tick.operator_id, machine.machine_type)
        behavior = BehaviorEngine(
            machine_id=machine.machine_id,
            operator_id=tick.operator_id,
            shift_id=tick.shift_id,
            rated_max_rpm=machine.rated_max_rpm,
            machine_type=machine.machine_type,
            baseline_median=median,
            baseline_mad=mad,
            shift_start=shift.scheduled_start,
        )
        behavior.set_ws_broadcast(self._broadcast)

        safety = SafetyEngine(machine.machine_id, machine.tilt_limit_degrees)
        safety.set_ws_broadcast(self._broadcast)
        if self._clock_driver is not None:
            safety.set_clock_driver(self._clock_driver)
        safety.add_incident_listener(_notify_behavior(behavior))
        safety.add_incident_listener(self._recommend_for_incident)

        ctx = MachineContext(
            shift_id=tick.shift_id,
            bucket_capacity=machine.bucket_capacity,
            safety=safety,
            behavior=behavior,
        )
        return ctx, current

    def _install(self, machine_id: str, ctx: MachineContext) -> None:
        safety_registry.register_engine(ctx.safety)
        behavior_registry.register_engine(ctx.behavior)
        self._contexts[machine_id] = ctx
        log.info("Edge engines ready", extra={"machine_id": machine_id, "shift_id": ctx.shift_id})

    async def _recommend_for_incident(
        self, incident_type: str, incident_id: str, tick: TelemetryTick, session: AsyncSession
    ) -> None:
        outcome = await recommend_for_source(
            session,
            operator_id=tick.operator_id,
            shift_id=tick.shift_id,
            source_type="incident",
            type_value=incident_type,
            source_id=incident_id,
            created_at=tick.timestamp,
        )
        if outcome is not None and outcome.created:
            # Sent after the tick commits, with the engines' own messages.
            self._effects.append({
                "type": "recommendation",
                "data": {
                    "module_id": outcome.module_id,
                    "module_title": outcome.module_title,
                    "source_type": "incident",
                    "source_value": incident_type,
                },
            })

    async def _safe_broadcast(self, machine_id: str, message: dict) -> None:
        try:
            await self._broadcast(machine_id, message)
        except Exception as exc:
            log.warning("Broadcast failed", extra={"machine_id": machine_id, "error": str(exc)})


def _notify_behavior(behavior: BehaviorEngine):
    async def listener(
        incident_type: str, incident_id: str, tick: TelemetryTick, session: AsyncSession
    ) -> None:
        await behavior.notify_incident(incident_type, tick.timestamp, session)

    return listener


async def _close_shift(ctx: MachineContext, at: datetime, session: AsyncSession) -> None:
    """End-of-shift check and, the first time only, the shift summary for the cloud."""
    if not await ctx.behavior.on_shift_end(at, session):
        return
    summary = await build_shift_summary(session, ctx.shift_id)
    if summary is not None:
        queue_shift_summary(session, summary)


def _completes_cycle(tick: TelemetryTick, task: EdgeTask) -> bool:
    return (
        task.status == TaskStatus.IN_PROGRESS.value
        and tick.load_cycles > 0
        and tick.cycle_payload_pct is not None
    )


def _engines(*contexts: MachineContext | None) -> list[Any]:
    return [engine for ctx in contexts if ctx is not None for engine in (ctx.safety, ctx.behavior)]


def _progress_message(task: EdgeTask) -> dict:
    completed = task.completed_quantity or 0.0
    return {
        "task_id": task.task_id,
        "status": task.status,
        "completed_quantity": completed,
        "target_quantity": task.target_quantity,
        "target_reached": completed >= task.target_quantity,
        # The one ETA the operator sees, and why it last changed ("weather", "pace", or null).
        "eta_minutes": displayed_eta(task.planning_eta, task.revised_predicted_time),
        "eta_revision_reason": task.revision_reason,
    }
