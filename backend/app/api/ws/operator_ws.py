"""Operator WebSocket endpoint.

WS /ws/operator/{machine_id}?token=<jwt>

On connect:
  1. Verify the token. An operator may only watch the machine of their
     current shift; an admin may watch any machine.
  2. Send a full state snapshot (shift, tasks, incidents, coaching,
     machine status). Clients request nothing else after a reconnect.
  3. Keep the connection open; the server pushes updates.

Messages pushed to the client:
  {"type": "state",          "data": snapshot}
  {"type": "telemetry",      "data": TelemetryTick}
  {"type": "incident",       "data": {incident_id, incident_type, severity, event, ...}}
  {"type": "progress",       "data": {task_id, status, completed_quantity, target_quantity, target_reached}}
  {"type": "coaching",       "data": {event_id, event_type, message, recommendation}}
  {"type": "recommendation", "data": {module_id, module_title, source_type, source_value}}
  {"type": "safety_degraded","data": {rule, machine_id}}
  {"type": "ping"}
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.operator_views import (
    machine_status_view,
    shift_coaching,
    shift_incidents,
    shift_tasks,
    shift_view,
)
from app.api.ws.manager import ws_manager
from app.core.security import decode_access_token
from app.edge.shift import current_shift_for_machine, current_shift_for_operator
from app.shared.enums import UserRole

log = logging.getLogger("safe2go.operator_ws")

_PING_SECONDS = 30.0
_CLOSE_UNAUTHORIZED = 4001
_CLOSE_FORBIDDEN = 4003

router = APIRouter(tags=["websocket"])


def create_ws_router(session_factory: async_sessionmaker) -> APIRouter:
    @router.websocket("/ws/operator/{machine_id}")
    async def operator_ws(
        machine_id: str,
        websocket: WebSocket,
        token: str = Query(..., description="JWT access token"),
    ) -> None:
        try:
            payload = decode_access_token(token)
        except Exception:
            await websocket.close(code=_CLOSE_UNAUTHORIZED)
            return

        async with session_factory() as session:
            if not await _may_watch(session, payload, machine_id):
                await websocket.close(code=_CLOSE_FORBIDDEN)
                return

        # Register first (updates are held), then read the snapshot, so an update
        # made while the snapshot is read is not lost.
        await ws_manager.connect(machine_id, websocket)
        try:
            async with session_factory() as session:
                snapshot = await build_snapshot(session, machine_id)
            await ws_manager.send_state_snapshot(machine_id, websocket, snapshot)
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=_PING_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        finally:
            await ws_manager.disconnect(machine_id, websocket)

    return router


async def _may_watch(session: AsyncSession, payload: dict, machine_id: str) -> bool:
    if payload.get("role") == UserRole.ADMIN.value:
        return True
    operator_id = payload.get("operator_id")
    if not operator_id:
        return False
    shift = await current_shift_for_operator(session, operator_id)
    return shift is not None and shift.machine_id == machine_id


async def build_snapshot(session: AsyncSession, machine_id: str) -> dict:
    """Full state for the operator view on connect or reconnect."""
    shift = await current_shift_for_machine(session, machine_id)
    status = machine_status_view(machine_id).model_dump(mode="json")
    if shift is None:
        return {"shift": None, "tasks": [], "incidents": [], "coaching": [], "status": status}
    return {
        "shift": (await shift_view(session, shift)).model_dump(mode="json"),
        "tasks": [t.model_dump(mode="json") for t in await shift_tasks(session, shift.shift_id)],
        "incidents": [i.model_dump(mode="json") for i in await shift_incidents(session, shift.shift_id)],
        "coaching": [c.model_dump(mode="json") for c in await shift_coaching(session, shift.shift_id)],
        "status": status,
    }
