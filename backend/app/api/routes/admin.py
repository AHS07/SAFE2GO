"""Admin routes: master data lists, operator accounts, shift and task assignment, ETA breakdown.

Every route requires the admin role. Writes are validated in the cloud
assignment service and audited in the same transaction.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.assignment import service as assignment
from app.cloud.audit.service import record_audit
from app.cloud.conflicts.service import list_conflicts, resolve_conflict
from app.cloud.credentials.accounts import create_operator_account, operator_accounts
from app.cloud.eta.service import eta_breakdown
from app.cloud.shift_report import cloud_shift_report
from app.config.thresholds import get_thresholds
from app.core.deps import require_admin
from app.core.errors import NotFoundError
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.task import Task
from app.db.session import get_db
from app.schemas.admin import (
    AssignmentResponse,
    AssignmentWarningResponse,
    ConflictResponse,
    DeadLetterResponse,
    EtaBreakdownResponse,
    MachineAdminResponse,
    OperatorAccountCreateRequest,
    OperatorAccountResponse,
    OperatorAdminResponse,
    QualificationResponse,
    RetryResponse,
    ShiftAdminResponse,
    ShiftCreateRequest,
    ShiftCreateResponse,
    TaskAdminResponse,
    TaskCreateRequest,
    TaskReassignRequest,
)
from app.schemas.maintenance import ServiceSuggestionResponse
from app.schemas.shift_report import ShiftReportResponse
from app.shared.enums import AuditAction
from app.shared.maintenance import service_suggestion
from app.sync.worker import list_dead_letters, retry_dead_letter

router = APIRouter(prefix="/api/admin", tags=["admin"])

_SHIFT_LIST_MAX = 500


def _delivery(entity_id: str, queued: set[str]) -> str:
    return "queued" if entity_id in queued else "delivered"


def _shift_view(shift: Shift, queued: set[str]) -> ShiftAdminResponse:
    view = ShiftAdminResponse.model_validate(shift, from_attributes=True)
    return view.model_copy(update={"delivery": _delivery(shift.shift_id, queued)})


def _task_view(task: Task, queued: set[str]) -> TaskAdminResponse:
    view = TaskAdminResponse.model_validate(task, from_attributes=True)
    return view.model_copy(update={"delivery": _delivery(task.task_id, queued)})


async def _assignment_view(session: AsyncSession, result: assignment.AssignmentResult) -> AssignmentResponse:
    queued = await assignment.queued_entities(session, [result.task.task_id])
    return AssignmentResponse(
        task=_task_view(result.task, queued),
        warnings=[AssignmentWarningResponse(**asdict(w)) for w in result.warnings],
    )


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------


@router.get("/operators", response_model=list[OperatorAdminResponse], dependencies=[Depends(require_admin)])
async def get_operators(session: AsyncSession = Depends(get_db)) -> list[OperatorAdminResponse]:
    accounts = await operator_accounts(session)
    return [
        OperatorAdminResponse(
            operator_id=op.operator_id,
            operator_name=op.operator_name,
            qualifications=[
                QualificationResponse(machine_type=q.machine_type, skill_level=q.skill_level) for q in quals
            ],
            username=accounts[op.operator_id].username if op.operator_id in accounts else None,
        )
        for op, quals in await assignment.list_operators(session)
    ]


@router.post("/operators/{operator_id}/account", response_model=OperatorAccountResponse, status_code=201)
async def post_operator_account(
    body: OperatorAccountCreateRequest,
    operator_id: str = Path(...),
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> OperatorAccountResponse:
    """Give an operator a password and PIN so they can sign in and see their assigned work."""
    result = await create_operator_account(
        session,
        operator_id,
        username=body.username,
        password=body.password,
        pin=body.pin,
        actor_id=user["sub"],
    )
    await session.commit()
    return OperatorAccountResponse(
        operator_id=operator_id,
        user_id=result.user.user_id,
        username=result.user.username,
        credentials_issued=result.credentials_issued,
    )


@router.get("/machines", response_model=list[MachineAdminResponse], dependencies=[Depends(require_admin)])
async def get_machines(session: AsyncSession = Depends(get_db)) -> list[MachineAdminResponse]:
    cfg = get_thresholds().maintenance
    return [
        MachineAdminResponse(
            machine_id=m.machine_id,
            machine_model=m.machine_model,
            machine_type=m.machine_type,
            machine_status=m.machine_status,
            machine_age=m.machine_age,
            bucket_capacity=m.bucket_capacity,
            service=ServiceSuggestionResponse(**asdict(service_suggestion(
                m.engine_hours, m.last_service_engine_hours, m.service_interval_hours, cfg
            ))),
        )
        for m in await assignment.list_machines(session)
    ]


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


@router.get("/shifts", response_model=list[ShiftAdminResponse], dependencies=[Depends(require_admin)])
async def get_shifts(
    start: datetime | None = Query(None, description="Shifts ending after this time"),
    end: datetime | None = Query(None, description="Shifts starting before this time"),
    operator_id: str | None = Query(None),
    machine_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_SHIFT_LIST_MAX),
    session: AsyncSession = Depends(get_db),
) -> list[ShiftAdminResponse]:
    shifts = await assignment.list_shifts(
        session, start=start, end=end, operator_id=operator_id, machine_id=machine_id, limit=limit
    )
    queued = await assignment.queued_entities(session, [s.shift_id for s in shifts])
    return [_shift_view(s, queued) for s in shifts]


@router.post("/shifts", response_model=ShiftCreateResponse, status_code=201)
async def post_shift(
    body: ShiftCreateRequest,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ShiftCreateResponse:
    result = await assignment.create_shift(
        session,
        operator_id=body.operator_id,
        machine_id=body.machine_id,
        scheduled_start=body.scheduled_start,
        scheduled_end=body.scheduled_end,
        weather_forecast=body.weather_forecast.value,
        actor_id=user["sub"],
    )
    await session.commit()
    view = _shift_view(result.shift, await assignment.queued_entities(session, [result.shift.shift_id]))
    return ShiftCreateResponse(
        **view.model_dump(),
        warnings=[AssignmentWarningResponse(**asdict(w)) for w in result.warnings],
    )


@router.get(
    "/shifts/{shift_id}/tasks",
    response_model=list[TaskAdminResponse],
    dependencies=[Depends(require_admin)],
)
async def get_shift_tasks(
    shift_id: str = Path(...),
    session: AsyncSession = Depends(get_db),
) -> list[TaskAdminResponse]:
    tasks = await assignment.list_shift_tasks(session, shift_id)
    queued = await assignment.queued_entities(session, [t.task_id for t in tasks])
    return [_task_view(t, queued) for t in tasks]


@router.get(
    "/shifts/{shift_id}/summary",
    response_model=ShiftReportResponse,
    dependencies=[Depends(require_admin)],
)
async def get_shift_summary(
    shift_id: str = Path(...),
    session: AsyncSession = Depends(get_db),
) -> ShiftReportResponse:
    return await cloud_shift_report(session, shift_id)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=AssignmentResponse, status_code=201)
async def post_task(
    body: TaskCreateRequest,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> AssignmentResponse:
    draft = assignment.TaskDraft(
        shift_id=body.shift_id,
        task_type=body.task_type.value,
        target_quantity=body.target_quantity,
        quantity_unit=body.quantity_unit.value,
        material_type=body.material_type.value,
        scheduled_start=body.scheduled_start,
    )
    result = await assignment.create_task(session, draft, actor_id=user["sub"])
    await session.commit()
    return await _assignment_view(session, result)


@router.post("/tasks/{task_id}/cancel", response_model=TaskAdminResponse)
async def post_cancel_task(
    task_id: str = Path(...),
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TaskAdminResponse:
    task = await assignment.cancel_task(session, task_id, actor_id=user["sub"])
    await session.commit()
    return _task_view(task, await assignment.queued_entities(session, [task.task_id]))


@router.post("/tasks/{task_id}/reassign", response_model=AssignmentResponse)
async def post_reassign_task(
    body: TaskReassignRequest,
    task_id: str = Path(...),
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> AssignmentResponse:
    result = await assignment.reassign_task(
        session,
        task_id,
        target_shift_id=body.target_shift_id,
        scheduled_start=body.scheduled_start,
        actor_id=user["sub"],
    )
    await session.commit()
    return await _assignment_view(session, result)


@router.get(
    "/tasks/{task_id}/eta",
    response_model=EtaBreakdownResponse,
    dependencies=[Depends(require_admin)],
)
async def get_task_eta(
    task_id: str = Path(...),
    session: AsyncSession = Depends(get_db),
) -> EtaBreakdownResponse:
    breakdown = await eta_breakdown(session, task_id)
    return EtaBreakdownResponse.model_validate(asdict(breakdown))


# ---------------------------------------------------------------------------
# Sync conflicts
# ---------------------------------------------------------------------------


def _conflict_view(conflict) -> ConflictResponse:  # noqa: ANN001 - SyncConflict row
    return ConflictResponse(
        conflict_id=conflict.conflict_id,
        task_id=conflict.task_id,
        conflict_type=conflict.conflict_type,
        edge_actual_start=conflict.edge_actual_start,
        cloud_changed_at=conflict.cloud_reassigned_at,
        created_at=conflict.created_at,
        resolved=conflict.resolved,
    )


@router.get("/conflicts", response_model=list[ConflictResponse], dependencies=[Depends(require_admin)])
async def get_conflicts(
    include_resolved: bool = Query(False),
    session: AsyncSession = Depends(get_db),
) -> list[ConflictResponse]:
    return [_conflict_view(c) for c in await list_conflicts(session, include_resolved)]


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictResponse)
async def post_resolve_conflict(
    conflict_id: str = Path(...),
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ConflictResponse:
    conflict = await resolve_conflict(session, conflict_id, user["sub"])
    await session.commit()
    return _conflict_view(conflict)


# ---------------------------------------------------------------------------
# Sync dead letters
# ---------------------------------------------------------------------------


@router.get("/sync/dead-letters", response_model=list[DeadLetterResponse], dependencies=[Depends(require_admin)])
async def get_dead_letters(session: AsyncSession = Depends(get_db)) -> list[DeadLetterResponse]:
    """Messages that kept failing and were set aside so later messages could continue."""
    return [DeadLetterResponse(**asdict(v)) for v in await list_dead_letters(session)]


@router.post("/sync/dead-letters/{message_id}/retry", response_model=RetryResponse)
async def post_retry_dead_letter(
    message_id: str = Path(...),
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> RetryResponse:
    """Put the message back at the head of its direction with a fresh retry window."""
    direction = await retry_dead_letter(session, message_id)
    if direction is None:
        raise NotFoundError(f"Dead letter {message_id} not found.", details={"message_id": message_id})
    record_audit(session, user["sub"], AuditAction.SYNC_RETRY, "sync_message", message_id)
    await session.commit()
    return RetryResponse(message_id=message_id, direction=direction)
