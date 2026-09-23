"""Loaders for static content files: training catalog, quizzes, emergency
guidance, and sample manuals.

Content lives in the directory set by settings.content_dir. Both tiers read
it: the cloud publishes the catalog, the edge serves modules, quizzes,
manuals, and emergency guidance from its local copy.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from app.config.settings import get_settings

_CATALOG_FILE = "training/catalog.yaml"
_EMERGENCY_FILE = "emergency/guidance.json"
_MANUAL_INDEX_FILE = "manuals/index.yaml"
_QUIZ_SUFFIX = ".quiz.json"


class CatalogModule(BaseModel):
    module_id: str = Field(max_length=36)
    title: str
    format: str
    duration_min: int = Field(gt=0)
    content_path: str


class CatalogMapEntry(BaseModel):
    source_type: str
    type_value: str
    module_id: str


class TrainingCatalog(BaseModel):
    modules: list[CatalogModule]
    recommendation_map: list[CatalogMapEntry]

    @model_validator(mode="after")
    def map_points_to_known_modules(self) -> TrainingCatalog:
        known = {m.module_id for m in self.modules}
        unknown = {e.module_id for e in self.recommendation_map} - known
        if unknown:
            raise ValueError(f"recommendation_map references unknown modules: {sorted(unknown)}")
        return self


class QuizQuestion(BaseModel):
    question: str
    options: list[str] = Field(min_length=2)
    answer: int
    explanation: str

    @model_validator(mode="after")
    def answer_in_range(self) -> QuizQuestion:
        if not 0 <= self.answer < len(self.options):
            raise ValueError(f"answer index {self.answer} out of range")
        return self


class Quiz(BaseModel):
    module_id: str
    questions: list[QuizQuestion] = Field(min_length=1)


class EmergencyItem(BaseModel):
    emergency_id: str
    title: str
    summary: str
    steps: list[str] = Field(min_length=1)


class EmergencyGuide(BaseModel):
    notice: str
    items: list[EmergencyItem] = Field(min_length=1)


class ManualEntry(BaseModel):
    manual_id: str
    title: str
    machine_type: str | None
    path: str


def content_dir() -> Path:
    return get_settings().content_dir


def read_text(relative_path: str) -> str:
    """Read a content file. Paths outside the content directory are rejected."""
    root = content_dir().resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Content path escapes the content directory: {relative_path}")
    return path.read_text(encoding="utf-8")


@lru_cache
def load_catalog() -> TrainingCatalog:
    return TrainingCatalog.model_validate(yaml.safe_load(read_text(_CATALOG_FILE)))


def quiz_path_for(content_path: str) -> str:
    """Quiz files sit next to the module markdown: name.md -> name.quiz.json."""
    return str(Path(content_path).with_suffix(_QUIZ_SUFFIX).as_posix())


@lru_cache
def load_quiz(content_path: str) -> Quiz:
    return Quiz.model_validate(json.loads(read_text(quiz_path_for(content_path))))


@lru_cache
def load_emergency_guide() -> EmergencyGuide:
    return EmergencyGuide.model_validate(json.loads(read_text(_EMERGENCY_FILE)))


@lru_cache
def load_manual_index() -> list[ManualEntry]:
    raw = yaml.safe_load(read_text(_MANUAL_INDEX_FILE))
    return [ManualEntry.model_validate(m) for m in raw["manuals"]]
