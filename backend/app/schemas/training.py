"""Pydantic schemas for the training hub, recommendations, and manual search."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ModuleSummary(BaseModel):
    module_id: str
    title: str
    format: str
    duration_min: int
    status: str                     # not_started | passed | needs_retry
    recommended: bool               # recommended during the current shift


class ModuleListResponse(BaseModel):
    parked: bool                    # content and quizzes open only while parked
    pass_pct: float
    modules: list[ModuleSummary]


class QuizQuestionPublic(BaseModel):
    """A quiz question without its answer."""

    question: str
    options: list[str]


class ModuleContentResponse(BaseModel):
    module_id: str
    title: str
    duration_min: int
    status: str
    body_markdown: str
    quiz: list[QuizQuestionPublic]


class QuizSubmitRequest(BaseModel):
    answers: list[int] = Field(min_length=1)


class QuizQuestionResult(BaseModel):
    correct: bool
    correct_option: int
    explanation: str


class QuizResultResponse(BaseModel):
    module_id: str
    score: float
    passed: bool
    pass_pct: float
    status: str
    questions: list[QuizQuestionResult]


class RecommendationResponse(BaseModel):
    recommendation_id: str
    module_id: str
    module_title: str
    source_type: str
    created_at: datetime
    status: str


class ManualSearchHit(BaseModel):
    manual_id: str
    manual_title: str
    machine_type: str | None
    section_title: str
    text: str
    score: float


class ManualSearchResponse(BaseModel):
    query: str
    results: list[ManualSearchHit]
