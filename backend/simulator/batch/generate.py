"""Batch dataset generator.

Usage:
    python -m simulator.batch.generate --seed 42

Generates the full 1,000-shift synthetic dataset with tick-by-tick telemetry,
derived actuals, injected anomalies, and shift summaries. Writes everything
to PostgreSQL using the cloud and edge schemas.

Generation order (matches architecture.md §9):
  1. Master data: machines, operators, qualifications
  2. Shifts (50 days x 20 machines = 1,000 shifts)
  3. Tasks per shift (3-5 each)
  4. Anomaly injection (picks shifts, records ground-truth windows)
  5. Tick-by-tick telemetry (edge schema)
  6. Derived actuals: completed_quantity, actual_start/end, paused/blocked
  7. Shift summaries (cloud schema)
  8. Edge replay: behavior events from the edge detectors (edge schema)
  9. High idle ratio events against baseline-window history, then the
     cloud behavior baselines from all history
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

import numpy as np
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cloud.baselines.service import compute_and_store_baselines
from app.cloud.maintenance import recompute_hour_meters
from app.config.settings import get_settings
from app.core.logging import setup_logging
from app.db.models.cloud.analytics import InjectedAnomaly, ShiftSummary
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.db.models.edge.behavior import BehaviorEvent
from app.db.models.edge.telemetry import Telemetry
from app.edge.behavior.detectors.excessive_idling import DetectionEvent
from app.shared.enums import TaskStatus
from simulator.batch.anomalies import inject_anomalies
from simulator.batch.edge_replay import ShiftIdleRecord, idle_ratio_events, replay_shift
from simulator.batch.master_data import generate_machines, generate_operators
from simulator.batch.shifts import EVALUATION_SPLIT_START, ShiftRecord, TaskRecord, generate_shifts
from simulator.batch.summaries import derive_actuals
from simulator.batch.telemetry import generate_telemetry

log = logging.getLogger("safe2go.generator")

_TELEMETRY_COLUMNS = (
    "timestamp", "machine_id", "operator_id", "task_id", "shift_id",
    "engine_running", "engine_rpm", "engine_hours", "fuel_used",
    "hydraulic_active", "machine_speed", "load_cycles", "payload_pct",
    "cycle_payload_pct", "seatbelt_status", "seat_occupied", "park_brake",
    "gear_state", "proximity_distance", "proximity_sensor_ok", "ambient_temp", "visibility", "tilt_angle",
)


async def run(seed: int) -> None:
    setup_logging("INFO")
    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rng = np.random.default_rng(seed)
    log.info("Generator started", extra={"seed": seed})

    async with session_factory() as session:
        # ------------------------------------------------------------------
        # 1. Master data
        # ------------------------------------------------------------------
        log.info("Generating master data")
        machines = generate_machines(rng)
        operators = generate_operators(rng)

        for m in machines:
            session.add(Machine(
                machine_id=m.machine_id,
                machine_model=m.machine_model,
                machine_type=m.machine_type.value,
                machine_age=m.machine_age,
                machine_status=m.machine_status.value,
                service_interval_hours=m.service_interval_hours,
                last_service_engine_hours=m.last_service_engine_hours,
                engine_hours=m.last_service_engine_hours,
                bucket_capacity=m.bucket_capacity,
                tilt_limit_degrees=m.tilt_limit_degrees,
                rated_max_rpm=m.rated_max_rpm,
            ))

        for op in operators:
            session.add(Operator(operator_id=op.operator_id, operator_name=op.operator_name))
            for machine_type, skill in op.qualifications:
                session.add(OperatorQualification(
                    operator_id=op.operator_id,
                    machine_type=machine_type.value,
                    skill_level=skill.value,
                ))

        await session.commit()
        log.info("Master data committed", extra={"machines": len(machines), "operators": len(operators)})

        # ------------------------------------------------------------------
        # 2 & 3. Shifts and tasks
        # ------------------------------------------------------------------
        log.info("Generating shifts and tasks")
        shifts = generate_shifts(machines, operators, rng)

        for s in shifts:
            session.add(Shift(
                shift_id=s.shift_id,
                machine_id=s.machine_id,
                operator_id=s.operator_id,
                date=s.date,
                scheduled_start=s.scheduled_start,
                scheduled_end=s.scheduled_end,
                weather_forecast=s.weather_forecast.value,
                weather_actual=s.weather_actual.value,
            ))

        for s in shifts:
            for t in s.tasks:
                session.add(Task(
                    task_id=t.task_id,
                    shift_id=t.shift_id,
                    task_type=t.task_type.value,
                    operator_skill_at_assignment=t.operator_skill_at_assignment.value,
                    target_quantity=t.target_quantity,
                    quantity_unit=t.quantity_unit.value,
                    material_type=t.material_type,
                    scheduled_start=t.scheduled_start,
                    scheduled_end=t.scheduled_end,
                    raw_predicted_time=t.raw_predicted_time,
                    planning_eta=t.planning_eta,
                    is_fallback=True,
                ))

        await session.commit()
        log.info(
            "Shifts and tasks committed",
            extra={"shifts": len(shifts), "tasks": sum(len(s.tasks) for s in shifts)},
        )

        # ------------------------------------------------------------------
        # 4. Anomaly injection (ground truth only)
        # ------------------------------------------------------------------
        log.info("Injecting anomalies")
        anomaly_records, anomaly_windows = inject_anomalies(shifts, rng)

        for a in anomaly_records:
            session.add(InjectedAnomaly(
                anomaly_id=a.anomaly_id,
                shift_id=a.shift_id,
                injected_anomaly_type=a.injected_anomaly_type,
                event_start=a.event_start,
                event_end=a.event_end,
                notes=a.notes,
            ))
        await session.commit()
        log.info("Anomalies committed", extra={"count": len(anomaly_records)})

        # ------------------------------------------------------------------
        # 5, 6, 7. Telemetry, actuals, summaries
        # ------------------------------------------------------------------
        machine_lookup = {m.machine_id: m for m in machines}
        idle_records: list[ShiftIdleRecord] = []

        for shift_num, shift in enumerate(shifts, 1):
            machine = machine_lookup[shift.machine_id]
            windows = anomaly_windows.get(shift.shift_id, [])

            ticks: list[dict] = []
            for tick in generate_telemetry(shift, machine, rng, anomaly_windows=windows):
                tick["_bucket_capacity"] = machine.bucket_capacity
                ticks.append(tick)

            updated_tasks, summary = derive_actuals(shift, ticks)

            await _copy_telemetry(session, ticks)

            # Task outcomes: finished tasks are done (the operator confirmed them),
            # tasks never started are cancelled, and a task still short of its
            # target after overtime stays paused.
            for task in updated_tasks:
                await session.execute(
                    update(Task)
                    .where(Task.task_id == task.task_id)
                    .values(
                        status=_history_status(task),
                        actual_start=task.actual_start,
                        actual_end=task.actual_end,
                        paused_minutes=task.paused_minutes,
                        blocked_minutes=task.blocked_minutes,
                        completed_quantity=task.completed_quantity,
                    )
                )

            for event in replay_shift(ticks, machine.rated_max_rpm, machine.machine_type.value):
                session.add(_behavior_row(shift, event))
            idle_records.append(ShiftIdleRecord(
                shift_id=shift.shift_id,
                operator_id=shift.operator_id,
                machine_type=machine.machine_type.value,
                start=shift.scheduled_start,
                end=ticks[-1]["timestamp"] if ticks else shift.scheduled_end,
                idle_ratio=summary.idle_ratio,
                in_baseline_window=shift.scheduled_start < EVALUATION_SPLIT_START,
            ))

            # Insert shift summary
            session.add(ShiftSummary(
                shift_id=summary.shift_id,
                engine_hours=summary.engine_hours,
                idle_minutes=summary.idle_minutes,
                idle_ratio=summary.idle_ratio,
                cycle_count=summary.cycle_count,
                completed_quantity=summary.completed_quantity,
                fuel_used=summary.fuel_used,
                cycle_rate=summary.cycle_rate,
            ))

            await session.commit()

            if shift_num % 100 == 0:
                log.info("Progress", extra={"shifts_done": shift_num, "total": len(shifts)})

        # ------------------------------------------------------------------
        # 9. High idle ratio events, then cloud baselines for live use
        # ------------------------------------------------------------------
        shift_lookup = {s.shift_id: s for s in shifts}
        for shift_id, event in idle_ratio_events(idle_records).items():
            session.add(_behavior_row(shift_lookup[shift_id], event))
        await compute_and_store_baselines(session)
        # Hour meters: last service reading plus the history just generated.
        await recompute_hour_meters(session)
        await session.commit()

    await engine.dispose()
    log.info("Generation complete", extra={"seed": seed, "shifts": len(shifts)})


async def _copy_telemetry(session: AsyncSession, ticks: list[dict]) -> None:
    """Write a shift's ticks with PostgreSQL COPY, inside the session's transaction.

    About 29 million rows in total; row inserts would take hours.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    await raw.driver_connection.copy_records_to_table(
        Telemetry.__tablename__,
        schema_name=Telemetry.__table_args__["schema"],
        columns=["telemetry_id", *_TELEMETRY_COLUMNS],
        records=[(str(uuid.uuid4()), *(tick[c] for c in _TELEMETRY_COLUMNS)) for tick in ticks],
    )


def _history_status(task: TaskRecord) -> str:
    if task.actual_start is None:
        return TaskStatus.CANCELLED.value
    if task.completed_quantity >= task.target_quantity:
        return TaskStatus.DONE.value
    return TaskStatus.PAUSED.value


def _behavior_row(shift: ShiftRecord, event: DetectionEvent) -> BehaviorEvent:
    return BehaviorEvent(
        event_id=str(uuid.uuid4()),
        machine_id=shift.machine_id,
        operator_id=shift.operator_id,
        task_id=None,
        shift_id=shift.shift_id,
        event_type=event.event_type,
        start=event.start,
        end=event.end,
        magnitude=event.magnitude,
        baseline_value=event.baseline_value,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic SAFE2GO dataset")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()
    asyncio.run(run(args.seed))


if __name__ == "__main__":
    main()
