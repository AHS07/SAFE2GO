"""Telemetry adapter interface.

All telemetry sources (live simulator, future real telematics via MQTT/CAN bus)
must implement TelemetryAdapter. The rest of the system only depends on
this interface, never on a concrete source.

Hardware Telematics Integration Note:
When connecting a physical in-cab CAN bus or MQTT telemetry feed:
- Telemetry ticks are normalized into `TelemetryTick` schema.
- Ticks are fed directly to `EdgePipeline.process(tick)`.
- The simulation clock driver and auto-slow hooks are strictly simulation-only
  fixtures and are bypassed entirely in hardware production mode.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.schemas.telemetry import TelemetryTick


class TelemetryAdapter(ABC):
    """Abstract source of telemetry ticks."""

    @abstractmethod
    async def ticks(self) -> AsyncIterator[TelemetryTick]:
        """Yield validated telemetry ticks as they arrive."""
        ...  # pragma: no cover

    @abstractmethod
    async def start(self) -> None:
        """Start producing ticks."""
        ...  # pragma: no cover

    @abstractmethod
    async def stop(self) -> None:
        """Stop producing ticks cleanly."""
        ...  # pragma: no cover


class SimulatorAdapter(TelemetryAdapter):
    """Telemetry adapter that reads from the live simulator queue.

    The live simulator pushes TelemetryTick objects into a shared
    asyncio.Queue. This adapter pulls from that queue.
    """

    def __init__(self, queue: asyncio.Queue[TelemetryTick | None]) -> None:
        self._queue: asyncio.Queue[TelemetryTick | None] = queue
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        await self._queue.put(None)   # sentinel to unblock the consumer

    async def ticks(self) -> AsyncIterator[TelemetryTick]:
        while True:
            tick = await self._queue.get()
            if tick is None:
                break
            yield tick
