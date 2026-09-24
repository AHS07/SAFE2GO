"""Rehearse the Kafka pipeline under a reconnect with live load.

Needs the backend running with KAFKA_ENABLED=true and Kafka up
(docker compose up -d kafka). Each run reseeds the demo, then:

  1. Pipeline healthy: forwarder up to date, consumer running.
  2. Live ticks from every demo machine reach the analytics archive.
  3. Link cut: the spool grows; safety still works end to end (proximity
     to CRITICAL, clear, acknowledge) with no broker in the path.
  4. Link restored: the backlog drains at or below the replay cap while the
     simulator keeps running; operator reads stay fast and live data keeps
     arriving.
  5. Simulator stopped: every live tick is in the archive exactly once
     (archive rows == live edge telemetry rows).

    python -m scripts.pipeline_rehearsal --offline-seconds 60 --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import get_settings
from app.db.models.cloud.stream import TelemetryArchive
from app.db.models.edge.telemetry import Telemetry
from scripts.demo_rehearsal import FAST_SPEED, Demo, RehearsalError, wait_for
from simulator.batch.shifts import N_DAYS, START_DATE

DRAIN_TIMEOUT_S = 240.0
# Share of the replay cap the measured send rate may reach (rate is a 10 s average).
RATE_TOLERANCE = 1.15


async def pipeline(demo: Demo) -> dict:
    return await demo.call("GET", "/api/admin/pipeline", demo.admin)


async def live_counts() -> tuple[int, int]:
    """(live edge telemetry rows, archive rows). Live = after the generated history."""
    from datetime import timedelta

    history_end = START_DATE + timedelta(days=N_DAYS)
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            live = (await conn.execute(
                select(func.count()).select_from(Telemetry).where(Telemetry.timestamp >= history_end)
            )).scalar_one()
            archived = (await conn.execute(select(func.count()).select_from(TelemetryArchive))).scalar_one()
    finally:
        await engine.dispose()
    return live, archived


async def rehearse(base_url: str, offline_seconds: float) -> dict:
    report: dict = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as http:
        demo = Demo(http, base_url)
        try:
            await demo.reset()
            await demo.step_sign_in()

            # 1. Healthy pipeline.
            first = await pipeline(demo)
            if not first["enabled"]:
                raise RehearsalError("KAFKA_ENABLED is false on the server")

            async def healthy() -> bool:
                p = await pipeline(demo)
                return p["consumer"]["state"] == "running" and p["forwarder"]["state"] in ("idle", "draining")
            await wait_for("forwarder and consumer connected", healthy, timeout=90)
            print("  ok  1 pipeline healthy", flush=True)

            # 2. Live ticks reach the archive.
            await demo.call("POST", "/api/sim/start")
            await demo.call("POST", "/api/sim/speed", json={"speed": FAST_SPEED})

            async def archived() -> bool:
                p = await pipeline(demo)
                return p["archive"]["machines"] >= 3 and p["archive"]["ticks"] > 100
            await wait_for("live ticks from 3 machines in the archive", archived, timeout=90)
            print("  ok  2 live ticks archived from every demo machine", flush=True)

            # 3. Link cut: spool grows, safety unaffected.
            await demo.call("POST", "/api/system/connectivity", json={"cloud_reachable": False})
            started = time.monotonic()
            await demo.step_proximity()
            report["safety_offline_s"] = round(time.monotonic() - started, 1)
            await demo.call("POST", "/api/sim/speed", json={"speed": FAST_SPEED})
            remaining = offline_seconds - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            offline = await pipeline(demo)
            if offline["forwarder"]["state"] != "link_down":
                raise RehearsalError(f"forwarder should report link_down, got {offline['forwarder']['state']}")
            report["backlog_peak"] = offline["spool"]["total"]
            report["oldest_age_s"] = offline["spool"]["oldest_age_seconds"]
            if report["backlog_peak"] < 100:
                raise RehearsalError(f"spool should grow while offline, only {report['backlog_peak']}")
            print(
                f"  ok  3 offline {offline_seconds:.0f} s: {report['backlog_peak']} records spooled, "
                f"proximity handled in {report['safety_offline_s']} s",
                flush=True,
            )

            # 4. Reconnect under load.
            await demo.call("POST", "/api/system/connectivity", json={"cloud_reachable": True})
            cap = offline["forwarder"]["max_rate"]
            reconnect = time.monotonic()
            backlog, rates, read_ms = [], [], []
            last_status_ts = None
            status_changes = 0
            deadline = reconnect + DRAIN_TIMEOUT_S
            while time.monotonic() < deadline:
                t0 = time.perf_counter()
                status = await demo.call("GET", "/api/operator/status", demo.op)
                read_ms.append((time.perf_counter() - t0) * 1000)
                if status["timestamp"] != last_status_ts:
                    status_changes += 1
                    last_status_ts = status["timestamp"]
                p = await pipeline(demo)
                backlog.append(p["spool"]["total"])
                rates.append(p["forwarder"]["send_rate"])
                if p["spool"]["total"] < 200 and time.monotonic() - reconnect > 3:
                    break
                await asyncio.sleep(1.0)
            else:
                raise RehearsalError(f"backlog did not drain in {DRAIN_TIMEOUT_S:.0f} s: {backlog[-5:]}")
            report["drain_s"] = round(time.monotonic() - reconnect, 1)
            report["peak_send_rate"] = max(rates)
            report["operator_read_p95_ms"] = round(statistics.quantiles(read_ms, n=20)[-1], 1) if len(read_ms) > 1 else read_ms[0]
            if report["peak_send_rate"] > cap * RATE_TOLERANCE:
                raise RehearsalError(f"send rate {report['peak_send_rate']}/s exceeded the cap {cap}/s")
            if status_changes < max(2, len(read_ms) // 2):
                raise RehearsalError("live sensor data stalled during the drain")
            print(
                f"  ok  4 drained {report['backlog_peak']} in {report['drain_s']} s, peak {report['peak_send_rate']}/s "
                f"(cap {cap:.0f}/s), operator reads p95 {report['operator_read_p95_ms']} ms",
                flush=True,
            )

            # 5. Exactly once in the archive.
            await demo.call("POST", "/api/sim/stop")

            async def settled() -> tuple[int, int] | None:
                p = await pipeline(demo)
                if p["spool"]["total"] or p["consumer"]["lag"]:
                    return None
                live, stored = await live_counts()
                return (live, stored) if live == stored else None
            try:
                live, stored = await wait_for("archive to match live telemetry", settled, timeout=90)
            except RehearsalError:
                live, stored = await live_counts()
                raise RehearsalError(f"archive has {stored} rows for {live} live ticks") from None
            final = await pipeline(demo)
            report["live_ticks"] = live
            report["duplicates_ignored"] = final["consumer"]["duplicates_total"]
            print(f"  ok  5 archive holds all {stored} live ticks exactly once", flush=True)
        finally:
            await demo.call("POST", "/api/system/connectivity", json={"cloud_reachable": True})
            await demo.finish()
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse the Kafka pipeline under a reconnect with live load.")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--offline-seconds", type=float, default=60.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    for run in range(1, args.runs + 1):
        print(f"Run {run} of {args.runs}", flush=True)
        try:
            report = await rehearse(args.base_url, args.offline_seconds)
        except RehearsalError as exc:
            print(f"  FAILED: {exc}", flush=True)
            return 1
        print(f"  run {run} passed: {report}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
