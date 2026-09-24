"""Seed, or reseed, the demo.

Creates:
  - One admin user
  - Three demo operators (beginner, intermediate, expert) with a password and a PIN
  - One demo shift per operator, each on its own excavator, with two tasks.
    Shifts start 1 hour before the current sim time and run 8 hours, so the
    offline credentials are valid whenever the demo runs.
  - Training catalog in the cloud schema
  - The expert's machine set close to its service interval, so a service
    suggestion shows in the demo

Everything reaches the edge the same way as in production: the cloud queues
messages in its outbox and the sync step delivers them.

Rerunning first removes the previous demo run (live shifts, demo users,
synced edge copies, outboxes, audit log) and keeps the generated history,
so a clean demo takes seconds instead of a full regenerate.

Run after the batch generator and training:
    python -m scripts.seed_demo
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cloud.assignment.service import TaskDraft, create_shift, create_task
from app.cloud.publish import queue_reference_snapshots
from app.cloud.training.catalog import publish_catalog
from app.config.settings import get_settings
from app.core.clock import sim_now
from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.models.cloud.analytics import ShiftSummary, SyncConflict
from app.db.models.cloud.edge_records import CloudBehaviorEvent, CloudIncident
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.sync import CloudDeadLetter, CloudInbox, CloudOutbox
from app.db.models.cloud.task import Task
from app.db.models.cloud.training import QuizResult
from app.db.models.cloud.user import AuditLog, OfflineCredential, User
from app.db.models.edge.assignment import (
    EdgeBehaviorBaseline,
    EdgeEtaAggregate,
    EdgeMachine,
    EdgeOfflineCredential,
    EdgeOperator,
    EdgeOperatorQualification,
    EdgeShift,
    EdgeTask,
)
from app.db.models.edge.behavior import BehaviorEvent, Recommendation
from app.db.models.edge.incident import Incident
from app.db.models.edge.sync import EdgeDeadLetter, EdgeInbox, EdgeOutbox
from app.db.models.edge.telemetry import Telemetry
from app.db.models.edge.training import EdgeAnomalyTrainingMap, EdgeQuizResult, EdgeTrainingModule
from app.shared.enums import (
    MachineType,
    MaterialType,
    QuantityUnit,
    SkillLevel,
    TaskType,
    UserRole,
    WeatherCategory,
)
from app.sync.worker import deliver_all
from simulator.batch.shifts import N_DAYS, START_DATE

log = logging.getLogger("safe2go.seed_demo")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
DEMO_PASSWORD = "demo123"
DEMO_PIN = "1234"
# Share of the service interval used on the expert's machine: above the due-soon threshold (0.90).
DEMO_SERVICE_USED_FRACTION = 0.92

_DEMO_OPERATORS = [
    ("op_beginner", "Alex Beginner", SkillLevel.BEGINNER),
    ("op_intermediate", "Sam Intermediate", SkillLevel.INTERMEDIATE),
    ("op_expert", "Jordan Expert", SkillLevel.EXPERT),
]

_DEMO_TASKS = [
    (TaskType.EXCAVATION, 120.0, QuantityUnit.M3, MaterialType.CLAY),
    (TaskType.MATERIAL_LOADING, 60.0, QuantityUnit.LOADS, MaterialType.GRAVEL),
]

_SHIFT_LEAD = timedelta(hours=1)
_SHIFT_LENGTH = timedelta(hours=8)

# Edge copies are rebuilt from the cloud snapshots on every seed.
_EDGE_COPIES = (
    EdgeMachine,
    EdgeOperator,
    EdgeOperatorQualification,
    EdgeShift,
    EdgeTask,
    EdgeBehaviorBaseline,
    EdgeEtaAggregate,
    EdgeOfflineCredential,
    EdgeAnomalyTrainingMap,
    EdgeTrainingModule,
    EdgeInbox,
    EdgeOutbox,
    EdgeDeadLetter,
)


async def clear_previous_demo(session: AsyncSession) -> None:
    """Remove everything a previous demo run created. Generated history is kept."""
    history_end = START_DATE + timedelta(days=N_DAYS)
    live_shifts = select(Shift.shift_id).where(Shift.scheduled_start >= history_end).scalar_subquery()
    demo_operators = select(User.linked_operator_id).where(User.role == UserRole.OPERATOR.value).scalar_subquery()

    for model in (Incident, BehaviorEvent, Recommendation, Telemetry):
        await session.execute(delete(model).where(model.shift_id.in_(live_shifts)))
    await session.execute(delete(EdgeQuizResult).where(EdgeQuizResult.operator_id.in_(demo_operators)))
    for model in _EDGE_COPIES:
        await session.execute(delete(model))

    for model in (SyncConflict, CloudIncident, CloudBehaviorEvent, AuditLog, CloudOutbox, CloudInbox, CloudDeadLetter):
        await session.execute(delete(model))
    for model in (Task, ShiftSummary, OfflineCredential):
        await session.execute(delete(model).where(model.shift_id.in_(live_shifts)))
    await session.execute(delete(Shift).where(Shift.shift_id.in_(live_shifts)))
    await session.execute(delete(QuizResult).where(QuizResult.operator_id.in_(demo_operators)))

    operator_ids = list((await session.execute(select(User.linked_operator_id).where(
        User.linked_operator_id.is_not(None)))).scalars().all())
    await session.execute(delete(User))
    await session.execute(delete(Operator).where(Operator.operator_id.in_(operator_ids)))
    await session.flush()
    log.info("Previous demo run cleared", extra={"demo_operators": len(operator_ids)})


async def _create_users(session: AsyncSession) -> tuple[User, list[tuple[Operator, SkillLevel]]]:
    admin = User(
        user_id=str(uuid.uuid4()),
        role=UserRole.ADMIN.value,
        linked_operator_id=None,
        username=ADMIN_USERNAME,
        password_hash=hash_password(ADMIN_PASSWORD),
    )
    session.add(admin)
    pin_hash = hash_password(DEMO_PIN)
    operators: list[tuple[Operator, SkillLevel]] = []
    for username, name, skill in _DEMO_OPERATORS:
        op = Operator(operator_id=str(uuid.uuid4()), operator_name=name)
        session.add(op)
        await session.flush()
        session.add(OperatorQualification(
            operator_id=op.operator_id, machine_type=MachineType.EXCAVATOR.value, skill_level=skill.value
        ))
        session.add(User(
            user_id=str(uuid.uuid4()),
            role=UserRole.OPERATOR.value,
            linked_operator_id=op.operator_id,
            username=username,
            password_hash=hash_password(DEMO_PASSWORD),
            pin_hash=pin_hash,
        ))
        operators.append((op, skill))
    await session.flush()
    return admin, operators


async def _demo_machines(session: AsyncSession, count: int) -> list[Machine]:
    """One available excavator per demo operator, so no machine is double-booked."""
    machines = list((
        await session.execute(
            select(Machine)
            .where(Machine.machine_type == MachineType.EXCAVATOR.value)
            .where(Machine.machine_status == "available")
            .order_by(Machine.machine_id)
            .limit(count)
        )
    ).scalars().all())
    if len(machines) < count:
        log.warning(
            "Not enough available excavators, skipping demo shift creation",
            extra={"needed": count, "found": len(machines)},
        )
        return []
    return machines


def _make_service_due_soon(machine: Machine) -> None:
    """Move the last service reading so the cockpit shows a service suggestion in the demo."""
    machine.last_service_engine_hours = round(
        machine.engine_hours - DEMO_SERVICE_USED_FRACTION * machine.service_interval_hours, 1
    )


async def _create_shifts(
    session: AsyncSession, admin: User, operators: list[tuple[Operator, SkillLevel]], machines: list[Machine]
) -> None:
    start = sim_now().replace(second=0, microsecond=0) - _SHIFT_LEAD
    for (op, _skill), machine in zip(operators, machines, strict=True):
        # Same validated path as the admin API: rules, ETA, audit, outbox, credential.
        shift = await create_shift(
            session,
            operator_id=op.operator_id,
            machine_id=machine.machine_id,
            scheduled_start=start,
            scheduled_end=start + _SHIFT_LENGTH,
            weather_forecast=WeatherCategory.CLEAR.value,
            actor_id=admin.user_id,
        )
        for task_type, quantity, unit, material in _DEMO_TASKS:
            await create_task(
                session,
                TaskDraft(
                    shift_id=shift.shift_id,
                    task_type=task_type.value,
                    target_quantity=quantity,
                    quantity_unit=unit.value,
                    material_type=material.value,
                ),
                actor_id=admin.user_id,
            )


async def run() -> None:
    setup_logging("INFO")
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        await clear_previous_demo(session)
        admin, operators = await _create_users(session)
        await publish_catalog(session)
        machines = await _demo_machines(session, len(operators))
        if machines:
            # The expert's machine (last in the list) is close to its service interval.
            _make_service_due_soon(machines[-1])
        # Snapshots go after the machine change so the edge gets the same readings.
        await queue_reference_snapshots(session)
        if machines:
            await _create_shifts(session, admin, operators, machines)
        await session.commit()

    results = await deliver_all(session_factory)
    await engine.dispose()
    log.info(
        "Demo seed complete",
        extra={name: result.delivered for name, result in results.items()},
    )


if __name__ == "__main__":
    asyncio.run(run())
