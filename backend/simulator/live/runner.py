"""Live demo runner.

Connects the clock driver to the edge pipeline: on every clock step each
streamed machine produces one tick, and the tick goes through the full
edge pipeline (ingest, safety, behavior, progress, recommendations).
Auto-slow works because the safety engines report open incidents to the
same clock driver.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.edge.assignment import EdgeOperator, EdgeShift
from app.edge.telemetry.pipeline import Broadcast, EdgePipeline
from simulator.live.clock_driver import ClockDriver
from simulator.live.stream import LiveStream

log = logging.getLogger("safe2go.live_runner")

# Clock steps buffered between the clock driver and the tick loop. If the
# loop falls behind, extra steps are dropped rather than piling up.
_STEP_QUEUE_SIZE = 10


class LiveRunner:
    def __init__(
        self,
        clock: ClockDriver,
        session_factory: async_sessionmaker,
        broadcast: Broadcast,
        seed: int,
    ) -> None:
        self._clock = clock
        self._session_factory = session_factory
        self._seed = seed
        self._pipeline = EdgePipeline(session_factory, broadcast, clock_driver=clock)
        self._streams: list[LiveStream] = []
        self._loop_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    @property
    def machine_ids(self) -> list[str]:
        return [s.machine_id for s in self._streams]

    async def start(self, machine_ids: list[str] | None = None) -> None:
        if self.is_running:
            return
        ids = machine_ids or await self._demo_machine_ids()
        self._streams = [LiveStream(mid, self._seed + i) for i, mid in enumerate(sorted(ids))]

        steps: asyncio.Queue[datetime] = asyncio.Queue(maxsize=_STEP_QUEUE_SIZE)
        await self._clock.start(on_tick=steps)
        self._loop_task = asyncio.create_task(self._run(steps), name="live_runner")
        log.info("Live runner started", extra={"machines": len(self._streams)})

    async def stop(self) -> None:
        if self._clock.is_running:
            await self._clock.stop()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        self._pipeline.reset()
        self._clock.reset_incidents()
        self._streams = []
        log.info("Live runner stopped")

    async def end_shift(self, machine_id: str, at: datetime) -> bool:
        """Run the end-of-shift check for a streamed machine (demo control)."""
        if machine_id not in self.machine_ids:
            return False
        return await self._pipeline.end_shift(machine_id, at)

    async def _run(self, steps: asyncio.Queue[datetime]) -> None:
        while True:
            ts = await steps.get()
            for stream in self._streams:
                await self._step(stream, ts)

    async def _step(self, stream: LiveStream, ts: datetime) -> None:
        try:
            async with self._session_factory() as session:
                tick = await stream.next_tick(ts, session)
        except Exception as exc:
            log.error(
                "Tick generation failed",
                extra={"machine_id": stream.machine_id, "error": str(exc)},
                exc_info=exc,
            )
            return
        if tick is not None:
            await self._pipeline.process(tick)

    async def _demo_machine_ids(self) -> list[str]:
        """Machines with a shift for an operator who can log in (the demo operators).

        Reads the edge copies: the edge knows which operators have accounts from the synced roster.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(EdgeShift.machine_id)
                .join(EdgeOperator, EdgeOperator.operator_id == EdgeShift.operator_id)
                .where(EdgeOperator.username.is_not(None))
                .distinct()
            )
            return list(result.scalars().all())
