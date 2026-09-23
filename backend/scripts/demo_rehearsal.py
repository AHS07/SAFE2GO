"""Rehearse the demo storyline (prd.md section 9) against a running backend.

Each run reseeds the demo, then checks every step and stops at the first
failure with a non-zero exit code:

  1. Operator signs in and sees the shift, tasks, and planning ETAs (REST and WebSocket).
  2. Operator starts a task; progress arrives live.
  3. Proximity scenario: auto-slow to 1x, CRITICAL, clears, acknowledged once stationary.
  4. Idling scenario: coaching card with a training recommendation.
  5. Cloud link cut: live data keeps flowing, PIN sign-in works, a new assignment is queued.
  6. Link restored: the task arrives; the admin ETA breakdown explains it.

The backend must run in dev or demo mode with the database it uses here.

    python -m scripts.demo_rehearsal --runs 3 --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx
import websockets

from app.core.clock import set_sim_time
from scripts import seed_demo

OPERATOR = "op_expert"
FAST_SPEED = 20
STEP_TIMEOUT_S = 150.0
POLL_S = 1.0


class RehearsalError(Exception):
    pass


async def wait_for(label: str, check: Callable[[], Awaitable[Any]], timeout: float = STEP_TIMEOUT_S) -> Any:
    """Poll check() until it returns something truthy, or fail after timeout seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(POLL_S)
    raise RehearsalError(f"timed out waiting for: {label}")


class Demo:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self.http = http
        self.base_url = base_url
        self.op: dict[str, str] = {}
        self.admin: dict[str, str] = {}
        self.shift: dict = {}

    async def call(self, method: str, path: str, headers: dict | None = None, **kwargs: Any) -> Any:
        response = await self.http.request(method, path, headers=headers, **kwargs)
        if response.is_error:
            raise RehearsalError(f"{method} {path} -> {response.status_code} {response.text[:200]}")
        return response.json()

    async def login(self, username: str, password: str) -> dict[str, str]:
        body = await self.call("POST", "/api/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {body['access_token']}"}

    async def incidents(self) -> list[dict]:
        return await self.call("GET", "/api/operator/incidents", self.op)

    async def tasks(self) -> list[dict]:
        return await self.call("GET", "/api/operator/tasks", self.op)

    # ------------------------------------------------------------------

    async def reset(self) -> None:
        await self.call("POST", "/api/sim/stop")
        await self.call("POST", "/api/system/connectivity", json={"cloud_reachable": True})
        # Seed on the server's sim time, which runs ahead of real time after fast-forwarding.
        status = await self.call("GET", "/api/sim/status")
        set_sim_time(datetime.fromisoformat(status["sim_time"]))
        await seed_demo.run()

    async def step_sign_in(self) -> None:
        self.op = await self.login(OPERATOR, seed_demo.DEMO_PASSWORD)
        self.admin = await self.login(seed_demo.ADMIN_USERNAME, seed_demo.ADMIN_PASSWORD)
        self.shift = await self.call("GET", "/api/operator/shift/current", self.op)
        tasks = await self.tasks()
        if len(tasks) != len(seed_demo._DEMO_TASKS) or any(t["eta_minutes"] is None for t in tasks):
            raise RehearsalError(f"expected {len(seed_demo._DEMO_TASKS)} tasks with ETAs, got {tasks}")
        token = self.op["Authorization"].split()[1]
        url = self.base_url.replace("http", "ws", 1) + f"/ws/operator/{self.shift['machine_id']}?token={token}"
        async with websockets.connect(url) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if first.get("type") != "state" or first["data"]["shift"]["shift_id"] != self.shift["shift_id"]:
            raise RehearsalError(f"unexpected WebSocket snapshot: {str(first)[:200]}")

    async def step_live_progress(self) -> None:
        await self.call("POST", "/api/sim/start")
        await self.call("POST", "/api/sim/speed", json={"speed": FAST_SPEED})
        task_id = (await self.tasks())[0]["task_id"]
        await self.call("POST", f"/api/operator/tasks/{task_id}/start", self.op)

        async def progressed() -> bool:
            task = next(t for t in await self.tasks() if t["task_id"] == task_id)
            return task["completed_quantity"] > 0
        await wait_for("task progress from live cycles", progressed)

    async def step_proximity(self) -> None:
        machine_id = self.shift["machine_id"]
        await self.call("POST", "/api/sim/scenarios/proximity", params={"machine_id": machine_id})

        async def critical() -> dict | None:
            return next((i for i in await self.incidents()
                         if i["incident_type"] == "proximity" and i["peak_severity"] == "critical"), None)
        incident = await wait_for("proximity incident at CRITICAL", critical)
        sim = await self.call("GET", "/api/sim/status")
        if sim["effective_speed"] != 1:
            raise RehearsalError(f"auto-slow expected 1x while an incident is open, got {sim['effective_speed']}")

        async def cleared() -> bool:
            current = next(i for i in await self.incidents() if i["incident_id"] == incident["incident_id"])
            return current["event_end"] is not None
        await wait_for("proximity hazard to clear", cleared)

        async def acknowledged() -> dict | None:
            response = await self.http.post(
                f"/api/operator/incidents/{incident['incident_id']}/acknowledge", headers=self.op
            )
            if response.status_code == httpx.codes.CONFLICT and response.json()["error"]["code"] == "ACK_REQUIRES_STATIONARY":
                return None      # machine repositioning; try again once it stops
            if response.is_error:
                raise RehearsalError(f"acknowledge failed: {response.text[:200]}")
            return response.json()
        result = await wait_for("acknowledgement while stationary", acknowledged)
        if result["status"] != "resolved":
            raise RehearsalError(f"cleared and acknowledged CRITICAL should resolve, got {result['status']}")

    async def step_idling(self) -> None:
        await self.call("POST", "/api/sim/speed", json={"speed": FAST_SPEED})
        await self.call("POST", "/api/sim/scenarios/idling", params={"machine_id": self.shift["machine_id"]})

        async def coached() -> dict | None:
            cards = await self.call("GET", "/api/operator/coaching", self.op)
            return next((c for c in cards if c["event_type"] == "excessive_idling"), None)
        await wait_for("excessive idling coaching card", coached)

        async def recommended() -> bool:
            recs = await self.call("GET", "/api/operator/recommendations", self.op)
            return any("fuel" in r["module_title"].lower() for r in recs)
        await wait_for("fuel efficiency training recommendation", recommended)

    async def step_offline(self) -> str:
        await self.call("POST", "/api/system/connectivity", json={"cloud_reachable": False})
        before = (await self.call("GET", "/api/operator/status", self.op))["timestamp"]

        async def ticking() -> bool:
            return (await self.call("GET", "/api/operator/status", self.op))["timestamp"] != before
        await wait_for("live sensor data while offline", ticking, timeout=30)

        pin = await self.http.post("/api/auth/offline-login", json={"username": OPERATOR, "pin": seed_demo.DEMO_PIN})
        if pin.is_error or not pin.json()["offline"]:
            raise RehearsalError(f"PIN sign-in failed while offline: {pin.text[:200]}")

        created = await self.call("POST", "/api/admin/tasks", self.admin, json={
            "shift_id": self.shift["shift_id"], "task_type": "trenching", "target_quantity": 10,
            "quantity_unit": "m3", "material_type": "gravel",
        })
        if created["task"]["delivery"] != "queued":
            raise RehearsalError("new assignment should be queued while offline")
        await asyncio.sleep(3)
        if any(t["task_id"] == created["task"]["task_id"] for t in await self.tasks()):
            raise RehearsalError("queued task reached the machine while offline")
        return created["task"]["task_id"]

    async def step_reconnect(self, task_id: str) -> None:
        await self.call("POST", "/api/system/connectivity", json={"cloud_reachable": True})

        async def arrived() -> bool:
            return any(t["task_id"] == task_id for t in await self.tasks())
        await wait_for("queued task to reach the operator", arrived, timeout=30)
        breakdown = await self.call("GET", f"/api/admin/tasks/{task_id}/eta", self.admin)
        if breakdown["raw_predicted_time"] is None or breakdown["planning_eta"] is None:
            raise RehearsalError(f"ETA breakdown incomplete: {breakdown}")

    async def finish(self) -> None:
        await self.call("POST", "/api/sim/stop")


async def rehearse(base_url: str) -> list[tuple[str, float]]:
    timings: list[tuple[str, float]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as http:
        demo = Demo(http, base_url)
        task_id = ""

        async def offline() -> None:
            nonlocal task_id
            task_id = await demo.step_offline()

        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("reset demo", demo.reset),
            ("1 sign in, shift, ETAs", demo.step_sign_in),
            ("2 live progress", demo.step_live_progress),
            ("3 proximity to CRITICAL, clear, acknowledge", demo.step_proximity),
            ("4 idling coaching and training", demo.step_idling),
            ("5 offline: safety data, PIN, queued task", offline),
            ("6 reconnect: task delivered, ETA breakdown", lambda: demo.step_reconnect(task_id)),
        ]
        try:
            for name, step in steps:
                started = time.monotonic()
                await step()
                timings.append((name, time.monotonic() - started))
                print(f"  ok  {name:<48} {timings[-1][1]:6.1f} s", flush=True)
        finally:
            await demo.finish()
    return timings


async def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse the SAFE2GO demo storyline.")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    for run in range(1, args.runs + 1):
        print(f"Run {run} of {args.runs}", flush=True)
        try:
            timings = await rehearse(args.base_url)
        except RehearsalError as exc:
            print(f"  FAILED: {exc}", flush=True)
            return 1
        print(f"  run {run} passed in {sum(t for _, t in timings):.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
