"""Training hub routes (edge tier).

The module list is always available. Module content and quiz submission
are available only while the operator's machine is parked.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import get_thresholds
from app.core.clock import sim_now
from app.core.deps import require_operator
from app.core.errors import MachineNotParkedError
from app.db.models.edge.assignment import EdgeShift
from app.db.session import get_db
from app.edge.shift import current_shift_for_operator
from app.edge.training import service
from app.edge.training.access import machine_is_parked, require_parked
from app.edge.training.recommendations import recommendations_for_shift
from app.schemas.training import (
    ModuleContentResponse,
    ModuleListResponse,
    ModuleSummary,
    QuizQuestionPublic,
    QuizQuestionResult,
    QuizResultResponse,
    QuizSubmitRequest,
)

router = APIRouter(prefix="/api/training", tags=["training"])


@router.get("/modules", response_model=ModuleListResponse)
async def list_modules(
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> ModuleListResponse:
    operator_id = operator_id_from(user)
    shift = await current_shift_for_operator(session, operator_id)

    recommended: set[str] = set()
    if shift is not None:
        recs = await recommendations_for_shift(session, operator_id, shift.shift_id)
        recommended = {module.module_id for _, module in recs}

    statuses = await service.statuses_for_operator(session, operator_id)
    modules = await service.list_modules(session)
    return ModuleListResponse(
        parked=shift is not None and machine_is_parked(shift.machine_id),
        pass_pct=get_thresholds().training.quiz_pass_pct,
        modules=[
            ModuleSummary(
                module_id=m.module_id,
                title=m.title,
                format=m.format,
                duration_min=m.duration_min,
                status=statuses.get(m.module_id, service.TrainingStatus.NOT_STARTED),
                recommended=m.module_id in recommended,
            )
            for m in modules
        ],
    )


@router.get("/modules/{module_id}", response_model=ModuleContentResponse)
async def get_module(
    module_id: str = Path(...),
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> ModuleContentResponse:
    operator_id = operator_id_from(user)
    require_parked_shift(await current_shift_for_operator(session, operator_id))

    module = await service.get_module(session, module_id)
    statuses = await service.statuses_for_operator(session, operator_id)
    quiz = service.module_quiz(module)
    return ModuleContentResponse(
        module_id=module.module_id,
        title=module.title,
        duration_min=module.duration_min,
        status=statuses.get(module.module_id, service.TrainingStatus.NOT_STARTED),
        body_markdown=service.module_body(module),
        quiz=[QuizQuestionPublic(question=q.question, options=q.options) for q in quiz.questions],
    )


@router.post("/modules/{module_id}/quiz", response_model=QuizResultResponse)
async def submit_quiz(
    body: QuizSubmitRequest,
    module_id: str = Path(...),
    user: dict = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
) -> QuizResultResponse:
    operator_id = operator_id_from(user)
    require_parked_shift(await current_shift_for_operator(session, operator_id))

    module = await service.get_module(session, module_id)
    grade, status = await service.submit_quiz(
        session,
        operator_id=operator_id,
        module=module,
        answers=body.answers,
        completed_at=sim_now(),
    )
    await session.commit()
    return QuizResultResponse(
        module_id=module.module_id,
        score=grade.score,
        passed=grade.passed,
        pass_pct=get_thresholds().training.quiz_pass_pct,
        status=status,
        questions=[
            QuizQuestionResult(
                correct=q.correct, correct_option=q.correct_option, explanation=q.explanation
            )
            for q in grade.questions
        ],
    )


def operator_id_from(user: dict) -> str:
    return user.get("operator_id") or user["sub"]


def require_parked_shift(shift: EdgeShift | None) -> None:
    if shift is None:
        raise MachineNotParkedError(
            "No current shift, so the machine state is unknown.",
            details={"reason": "no_shift"},
        )
    require_parked(shift.machine_id)
