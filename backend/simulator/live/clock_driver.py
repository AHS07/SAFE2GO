"""Live simulator clock driver.

Advances the simulation clock at the configured speed and implements
auto-slow: whenever any incident is open, the clock drops to 1x speed.
It returns to the selected speed once all incidents are resolved.

The clock driver runs as a background asyncio task.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

log = logging.getLogger("safe2go.clock_driver")


class ClockDriver:
    """Drives the simulation clock forward in real time.

    selected_speed: presenter-chosen multiplier (e.g. 5 = 5x).
    auto_slow:      when True, effective speed is 1x regardless of selected.
    """

    def __init__(self, tick_seconds: float = 1.0, selected_speed: int = 1) -> None:
        self._tick_seconds = tick_seconds          # sim seconds per tick
        self._selected_speed = selected_speed      # desired multiplier
        self._open_incident_count = 0
        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------

    def set_speed(self, speed: int) -> None:
        """Set the presenter-chosen speed multiplier."""
        self._selected_speed = max(1, speed)
        log.info("Clock speed set", extra={"selected_speed": self._selected_speed})

    def notify_incident_opened(self) -> None:
        self._open_incident_count += 1
        log.info(
            "Auto-slow activated",
            extra={"open_incidents": self._open_incident_count},
        )

    def reset_incidents(self) -> None:
        """Forget open incidents when the live engines are discarded."""
        self._open_incident_count = 0

    def notify_incident_closed(self) -> None:
        self._open_incident_count = max(0, self._open_incident_count - 1)
        if self._open_incident_count == 0:
            log.info("Auto-slow deactivated — all incidents resolved")

    @property
    def effective_speed(self) -> int:
        """Return 1 if any incident is open (auto-slow), else selected_speed."""
        return 1 if self._open_incident_count > 0 else self._selected_speed

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, on_tick: asyncio.Queue | None = None) -> None:
        """Start the clock driver loop as a background task."""
        self._running = True
        self._task = asyncio.create_task(self._run(on_tick), name="clock_driver")
        log.info("Clock driver started", extra={"tick_seconds": self._tick_seconds})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        log.info("Clock driver stopped")

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run(self, on_tick: asyncio.Queue | None) -> None:
        from app.core.clock import advance_sim_time

        while self._running:
            speed = self.effective_speed
            # Real-world sleep: tick_seconds / speed
            sleep_duration = self._tick_seconds / speed
            await asyncio.sleep(sleep_duration)

            new_time = advance_sim_time(self._tick_seconds)

            if on_tick is not None:
                # A slow consumer skips a step rather than blocking the clock.
                with contextlib.suppress(asyncio.QueueFull):
                    on_tick.put_nowait(new_time)
