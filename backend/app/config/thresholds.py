"""Typed loader for thresholds.yaml.

Reads the YAML file once and exposes nested Pydantic models.
All safety rules, behavior detectors, and ETA config must read
thresholds from here, never from hard-coded literals.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_THRESHOLDS_PATH = Path(__file__).parent / "thresholds.yaml"


# ---------------------------------------------------------------------------
# Safety models
# ---------------------------------------------------------------------------


class SeatbeltThresholds(BaseModel):
    warning_grace_seconds: int
    critical_grace_seconds: int
    cooldown_seconds: int


class OperatorNotSeatedThresholds(BaseModel):
    critical_grace_seconds: int
    cooldown_seconds: int


class ProximityThresholds(BaseModel):
    warning_trigger_meters: float
    warning_clear_meters: float
    critical_trigger_meters: float
    critical_clear_meters: float
    cooldown_seconds: int


class TiltThresholds(BaseModel):
    warning_trigger_fraction: float
    warning_clear_fraction: float
    critical_trigger_fraction: float
    critical_clear_fraction: float
    cooldown_seconds: int


class OverloadThresholds(BaseModel):
    warning_trigger_pct: float
    warning_clear_pct: float
    critical_trigger_pct: float
    critical_clear_pct: float
    cooldown_seconds: int


class VisibilityThresholds(BaseModel):
    warning_trigger_meters: float
    warning_clear_meters: float
    critical_trigger_meters: float
    critical_clear_meters: float
    cooldown_seconds: int


class AmbientTemperatureThresholds(BaseModel):
    warning_trigger_celsius: float
    warning_clear_celsius: float
    critical_trigger_celsius: float
    critical_clear_celsius: float
    cooldown_seconds: int


class RepeatEscalationConfig(BaseModel):
    applies_to: list[str]
    window_seconds: int
    count_threshold: int


class SafetyThresholds(BaseModel):
    debounce_seconds: int
    seatbelt: SeatbeltThresholds
    operator_not_seated: OperatorNotSeatedThresholds
    proximity: ProximityThresholds
    tilt: TiltThresholds
    overload: OverloadThresholds
    visibility: VisibilityThresholds
    ambient_temperature: AmbientTemperatureThresholds
    repeat_escalation: RepeatEscalationConfig


# ---------------------------------------------------------------------------
# Behavior models
# ---------------------------------------------------------------------------


class TravelSpeedThresholds(BaseModel):
    excavator: float
    wheel_loader: float
    backhoe_loader: float


class BehaviorThresholds(BaseModel):
    excessive_idling_seconds: int
    repeated_overloading_count: int
    repeated_seatbelt_count: int
    high_rpm_travel_sustained_seconds: int
    high_rpm_fraction: float
    travel_speed_threshold_kmh: TravelSpeedThresholds
    idle_ratio_mad_multiplier: float
    max_tick_gap_seconds: float = Field(gt=0)
    baseline_min_shifts: int


# ---------------------------------------------------------------------------
# ETA models
# ---------------------------------------------------------------------------


class ETAModelConfig(BaseModel):
    n_estimators: int
    max_depth: int
    random_state: int


class ETAThresholds(BaseModel):
    revision_pace_deviation_pct: float
    revision_completion_threshold_pct: float
    revision_rate_limit_seconds: int
    model: ETAModelConfig


# ---------------------------------------------------------------------------
# Training and assistant models
# ---------------------------------------------------------------------------


class TrainingThresholds(BaseModel):
    quiz_pass_pct: float = Field(ge=0, le=100)


class AssistantThresholds(BaseModel):
    search_top_k: int = Field(ge=1)
    search_min_score: float = Field(ge=0, le=1)
    explain_timeout_seconds: float = Field(gt=0)
    explain_retries: int = Field(ge=0)
    explain_max_tokens: int = Field(ge=50)
    explain_temperature: float = Field(ge=0, le=2)


class MaintenanceThresholds(BaseModel):
    due_soon_fraction: float = Field(gt=0, le=1)
    overdue_fraction: float = Field(gt=0)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class Thresholds(BaseModel):
    safety: SafetyThresholds
    behavior: BehaviorThresholds
    eta: ETAThresholds
    training: TrainingThresholds
    assistant: AssistantThresholds
    maintenance: MaintenanceThresholds


@lru_cache
def get_thresholds() -> Thresholds:
    """Load and validate thresholds.yaml. Cached after first call."""
    raw = yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    return Thresholds.model_validate(raw)
