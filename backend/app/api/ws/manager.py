"""WebSocket connection manager.

Maintains the set of active operator connections per machine_id.
Broadcasts state updates to all connected clients for a machine.

Message envelope:
  {"type": "telemetry" | "incident" | "progress" | "coaching" | "state", "data": {...}}
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("safe2go.ws_manager")


class ConnectionManager:
    """Thread-safe WebSocket connection registry."""

    def __init__(self) -> None:
        # machine_id -> set of active WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, machine_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[machine_id].add(ws)
        log.info("WS connected", extra={"machine_id": machine_id})

    async def disconnect(self, machine_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[machine_id].discard(ws)
        log.info("WS disconnected", extra={"machine_id": machine_id})

    async def broadcast(self, machine_id: str, message: dict) -> None:
        """Send a message to all connections for a machine. Dead connections are removed."""
        async with self._lock:
            connections = set(self._connections[machine_id])

        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections[machine_id].discard(ws)

    async def send_state_snapshot(self, machine_id: str, ws: WebSocket, state: dict) -> None:
        """Send a full state snapshot to a single newly-connected client."""
        try:
            await ws.send_json({"type": "state", "data": state})
        except Exception:
            await self.disconnect(machine_id, ws)

    def connection_count(self, machine_id: str) -> int:
        return len(self._connections.get(machine_id, set()))


# Module-level singleton used by all WS route handlers.
ws_manager = ConnectionManager()
