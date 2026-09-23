"""Training hub service (edge tier).

Serves modules and quizzes from the edge copy of the catalog, grades quiz
submissions, and derives training status from stored quiz results.
Training status is never stored; it is recomputed from results.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import get_thresholds
from app.core.errors import NotFoundError, ValidationError
from app.db.models.edge.training import EdgeQuizResult, EdgeTrainingModule
from app.edge.outbox import queue_quiz_result
from app.shared.content import Quiz, load_quiz, read_text

log = logging.getLogger("safe2go.training")


class TrainingStatus(StrEnum):
    NOT_STARTED = "not_started"
    PASSED = "passed"
    NEEDS_RETRY = "needs_retry"


@dataclass(frozen=True)
class QuestionResult:
    correct: bool
    correct_option: int
    explanation: str


@dataclass(frozen=True)
class QuizGrade:
    score: float                 # percent, 0 to 100
    passed: bool
    questions: list[QuestionResult]


def derive_status(scores: list[float], pass_pct: float) -> TrainingStatus:
    """Best attempt decides: any attempt at or above the pass mark is a pass."""
    if not scores:
        return TrainingStatus.NOT_STARTED
    return TrainingStatus.PASSED if max(scores) >= pass_pct else TrainingStatus.NEEDS_RETRY


def grade_quiz(quiz: Quiz, answers: list[int], pass_pct: float) -> QuizGrade:
    if len(answers) != len(quiz.questions):
        raise ValidationError(
            f"Expected {len(quiz.questions)} answers, received {len(answers)}.",
            details={"expected": len(quiz.questions), "received": len(answers)},
        )
    results = [
        QuestionResult(
            correct=answer == question.answer,
            correct_option=question.answer,
            explanation=question.explanation,
        )
        for question, answer in zip(quiz.questions, answers, strict=True)
    ]
    score = round(100.0 * sum(r.correct for r in results) / len(results), 1)
    return QuizGrade(score=score, passed=score >= pass_pct, questions=results)


async def list_modules(session: AsyncSession) -> list[EdgeTrainingModule]:
    result = await session.execute(select(EdgeTrainingModule).order_by(EdgeTrainingModule.title))
    return list(result.scalars().all())


async def get_module(session: AsyncSession, module_id: str) -> EdgeTrainingModule:
    module = await session.get(EdgeTrainingModule, module_id)
    if module is None or module.content_path is None:
        raise NotFoundError(f"Training module {module_id} not found.")
    return module


def module_body(module: EdgeTrainingModule) -> str:
    return read_text(module.content_path)


def module_quiz(module: EdgeTrainingModule) -> Quiz:
    return load_quiz(module.content_path)


async def statuses_for_operator(session: AsyncSession, operator_id: str) -> dict[str, TrainingStatus]:
    """Derived status per module_id. Modules with no attempts are absent."""
    result = await session.execute(
        select(EdgeQuizResult.module_id, EdgeQuizResult.score)
        .where(EdgeQuizResult.operator_id == operator_id)
    )
    scores: dict[str, list[float]] = {}
    for module_id, score in result.all():
        scores.setdefault(module_id, []).append(score)
    pass_pct = get_thresholds().training.quiz_pass_pct
    return {module_id: derive_status(s, pass_pct) for module_id, s in scores.items()}


async def submit_quiz(
    session: AsyncSession,
    *,
    operator_id: str,
    module: EdgeTrainingModule,
    answers: list[int],
    completed_at: datetime,
) -> tuple[QuizGrade, TrainingStatus]:
    """Grade, store the result, and return the grade with the updated status."""
    pass_pct = get_thresholds().training.quiz_pass_pct
    grade = grade_quiz(module_quiz(module), answers, pass_pct)

    result = EdgeQuizResult(
        result_id=str(uuid.uuid4()),
        operator_id=operator_id,
        module_id=module.module_id,
        score=grade.score,
        completed_at=completed_at,
    )
    session.add(result)
    await session.flush()
    queue_quiz_result(session, result)

    status = (await statuses_for_operator(session, operator_id)).get(
        module.module_id, TrainingStatus.NOT_STARTED
    )
    log.info(
        "Quiz submitted",
        extra={"operator_id": operator_id, "module_id": module.module_id, "score": grade.score},
    )
    return grade, status
