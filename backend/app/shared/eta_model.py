"""ETA model: feature building, prediction with fallback, and explanation.

One joblib artifact is used by both tiers: the cloud predicts at assignment
time, the edge revises mid-task. The offline scripts in ml/ build the
artifact with the same feature code, so training and serving cannot drift.

Features are limited to values known at assignment time (task type,
material, target quantity, skill, machine type and age, forecast weather)
plus historical aggregates.

If the artifact is missing, fails to load, or fails to predict, the
estimate falls back to the historical average and is marked is_fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.config.settings import get_settings
from app.shared.enums import MachineType, MaterialType, SkillLevel, TaskType, WeatherCategory
from app.shared.eta_aggregates import HistoryFeatures

log = logging.getLogger("safe2go.eta_model")

FALLBACK_MODEL_VERSION = "historical-average"
MIN_PREDICTED_MINUTES = 1.0

_SKILL_RANK: dict[str, float] = {
    SkillLevel.BEGINNER.value: 0.0,
    SkillLevel.INTERMEDIATE.value: 1.0,
    SkillLevel.EXPERT.value: 2.0,
}

_CATEGORICAL: dict[str, list[str]] = {
    "task_type": [e.value for e in TaskType],
    "material_type": [e.value for e in MaterialType],
    "machine_type": [e.value for e in MachineType],
    "weather": [e.value for e in WeatherCategory],
}

_NUMERIC: list[str] = [
    "target_quantity",
    "machine_age",
    "skill_rank",
    "operator_minutes_per_unit",
    "machine_minutes_per_unit",
    "operator_idle_ratio",
    "operator_cycle_rate",
    "historical_average_minutes",
]

# Features shown in the unusual-feature explanation, with their display names.
_EXPLAINED_NUMERIC: dict[str, str] = {
    "target_quantity": "Target quantity",
    "machine_age": "Machine age",
    "operator_minutes_per_unit": "Operator past pace",
    "operator_idle_ratio": "Operator idle ratio",
}
# Words for values above / below the typical value.
_COMPARISON_WORDS: dict[str, tuple[str, str]] = {
    "target_quantity": ("larger", "smaller"),
    "machine_age": ("older", "newer"),
    "operator_minutes_per_unit": ("slower", "faster"),
    "operator_idle_ratio": ("higher", "lower"),
}
_EXPLAINED_CATEGORICAL: dict[str, str] = {
    "weather": "Weather",
    "material_type": "Material",
    "skill_level": "Skill level",
}
_EXPLAIN_TOP_N = 3
_EXPLAIN_MIN_SCORE = 0.5
_QUANTILE_POINTS = 101


def feature_names() -> list[str]:
    names = list(_NUMERIC)
    for column, values in _CATEGORICAL.items():
        names.extend(f"{column}={v}" for v in values)
    return names


@dataclass(frozen=True)
class EtaInput:
    """Task facts known when the estimate is made.

    weather is the forecast at assignment and the observed category at
    revision time. target_quantity is the remaining quantity at revision.
    """

    task_type: str
    material_type: str
    target_quantity: float
    skill_level: str
    machine_type: str
    machine_age: int
    weather: str


def feature_row(inp: EtaInput, history: HistoryFeatures) -> dict[str, float]:
    """Build one feature row keyed by feature name."""
    row: dict[str, float] = {
        "target_quantity": inp.target_quantity,
        "machine_age": float(inp.machine_age),
        "skill_rank": _SKILL_RANK.get(inp.skill_level, _SKILL_RANK[SkillLevel.INTERMEDIATE.value]),
        "operator_minutes_per_unit": history.operator_minutes_per_unit,
        "machine_minutes_per_unit": history.machine_minutes_per_unit,
        "operator_idle_ratio": history.operator_idle_ratio,
        "operator_cycle_rate": history.operator_cycle_rate,
        "historical_average_minutes": history.historical_average_minutes(inp.target_quantity),
    }
    values = {
        "task_type": inp.task_type,
        "material_type": inp.material_type,
        "machine_type": inp.machine_type,
        "weather": inp.weather,
    }
    for column, categories in _CATEGORICAL.items():
        for category in categories:
            row[f"{column}={category}"] = 1.0 if values[column] == category else 0.0
    return row


def feature_matrix(rows: list[dict[str, float]]) -> np.ndarray:
    names = feature_names()
    return np.array([[r[n] for n in names] for r in rows], dtype=float)


# ---------------------------------------------------------------------------
# Unusual-feature explanation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnusualFeature:
    feature: str
    value: str
    description: str
    score: float


def build_reference(inputs: list[EtaInput], histories: list[HistoryFeatures]) -> dict[str, Any]:
    """Summarise the training distribution for the explanation.

    Numeric features: quantiles per task type. Categorical: frequencies.
    """
    quantiles = np.linspace(0.0, 1.0, _QUANTILE_POINTS)
    numeric: dict[str, dict[str, list[float]]] = {}
    for task_type in {i.task_type for i in inputs}:
        idx = [k for k, i in enumerate(inputs) if i.task_type == task_type]
        numeric[task_type] = {
            name: np.quantile(
                [_explained_value(inputs[k], histories[k], name) for k in idx], quantiles
            ).tolist()
            for name in _EXPLAINED_NUMERIC
        }
    categorical: dict[str, dict[str, float]] = {}
    for name in _EXPLAINED_CATEGORICAL:
        values = [getattr(i, name) for i in inputs]
        categorical[name] = {v: values.count(v) / len(values) for v in sorted(set(values))}
    return {"numeric": numeric, "categorical": categorical}


def _explained_value(inp: EtaInput, history: HistoryFeatures, name: str) -> float:
    if name in ("target_quantity", "machine_age"):
        return float(getattr(inp, name))
    return float(getattr(history, name))


def explain(
    inp: EtaInput,
    history: HistoryFeatures,
    reference: dict[str, Any],
    unit: str,
) -> list[UnusualFeature]:
    """Return the 2 to 3 feature values most unusual for this task.

    Scores are on a 0 to 1 scale: for numbers, the distance of the value's
    percentile from the median; for categories, how much rarer the value is
    than the most common one. Only clearly unusual values are returned.
    """
    found: list[UnusualFeature] = []
    task_quantiles = reference.get("numeric", {}).get(inp.task_type, {})
    for name, label in _EXPLAINED_NUMERIC.items():
        if name not in task_quantiles:
            continue
        value = _explained_value(inp, history, name)
        # Midpoint of the left and right positions, so a value equal to many
        # past values sits in the middle of them rather than at their edge.
        q = task_quantiles[name]
        position = (np.searchsorted(q, value, "left") + np.searchsorted(q, value, "right")) / 2.0
        percentile = float(position) / (_QUANTILE_POINTS - 1)
        percentile = min(max(percentile, 0.0), 1.0)
        score = abs(percentile - 0.5) * 2.0
        above = percentile >= 0.5
        side = _COMPARISON_WORDS[name][0 if above else 1]
        share = percentile if above else 1.0 - percentile
        found.append(UnusualFeature(
            feature=name,
            value=_format_value(name, value, unit),
            description=(
                f"{label} {_format_value(name, value, unit)} is {side} than "
                f"{share:.0%} of past {inp.task_type.replace('_', ' ')} tasks."
            ),
            score=score,
        ))
    for name, label in _EXPLAINED_CATEGORICAL.items():
        freqs = reference.get("categorical", {}).get(name, {})
        if not freqs:
            continue
        value = getattr(inp, name)
        freq = freqs.get(value, 0.0)
        found.append(UnusualFeature(
            feature=name,
            value=value,
            description=f"{label} '{value.replace('_', ' ')}' appears in {freq:.0%} of past tasks.",
            score=1.0 - freq / max(freqs.values()),
        ))
    found = [f for f in found if f.score >= _EXPLAIN_MIN_SCORE]
    found.sort(key=lambda f: f.score, reverse=True)
    return found[:_EXPLAIN_TOP_N]


def _format_value(name: str, value: float, unit: str) -> str:
    if name == "target_quantity":
        return f"{value:.0f} {unit}"
    if name == "machine_age":
        return f"{value:.0f} years"
    if name == "operator_minutes_per_unit":
        return f"{value:.2f} min per {unit}"
    return f"{value:.0%}"


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EtaEstimate:
    raw_minutes: float
    historical_average_minutes: float
    is_fallback: bool
    model_version: str
    unusual_features: list[UnusualFeature] = field(default_factory=list)


class EtaPredictor:
    """Wraps the loaded artifact. With no artifact it always falls back."""

    def __init__(self, artifact: dict[str, Any] | None) -> None:
        self._artifact = artifact

    @property
    def available(self) -> bool:
        return self._artifact is not None

    @property
    def model_version(self) -> str:
        return self._artifact["model_version"] if self._artifact else FALLBACK_MODEL_VERSION

    def predict_matrix(self, x: np.ndarray) -> np.ndarray:
        """Raw model output for a prepared feature matrix (evaluation only)."""
        if self._artifact is None:
            raise RuntimeError("No ETA model loaded.")
        return self._artifact["model"].predict(x)

    def predict(self, inp: EtaInput, history: HistoryFeatures, unit: str) -> EtaEstimate:
        historical = max(history.historical_average_minutes(inp.target_quantity), MIN_PREDICTED_MINUTES)
        if self._artifact is None:
            return EtaEstimate(historical, historical, True, FALLBACK_MODEL_VERSION)
        try:
            x = feature_matrix([feature_row(inp, history)])
            raw = float(self._artifact["model"].predict(x)[0])
            unusual = explain(inp, history, self._artifact["reference"], unit)
        except Exception as exc:
            log.error("ETA prediction failed, using historical average", exc_info=exc)
            return EtaEstimate(historical, historical, True, FALLBACK_MODEL_VERSION)
        return EtaEstimate(
            raw_minutes=round(max(raw, MIN_PREDICTED_MINUTES), 1),
            historical_average_minutes=round(historical, 1),
            is_fallback=False,
            model_version=self.model_version,
            unusual_features=unusual,
        )


_REQUIRED_KEYS = {"model", "feature_names", "model_version", "reference"}


def load_predictor(path: Path) -> EtaPredictor:
    """Load the artifact. Any failure gives a fallback-only predictor, never an error."""
    try:
        artifact = joblib.load(path)
        missing = _REQUIRED_KEYS - set(artifact)
        if missing:
            raise ValueError(f"artifact missing keys: {sorted(missing)}")
        if artifact["feature_names"] != feature_names():
            raise ValueError("artifact features do not match the current feature builder")
    except FileNotFoundError:
        log.warning("ETA model artifact not found, using historical averages", extra={"path": str(path)})
        return EtaPredictor(None)
    except Exception as exc:
        log.error("ETA model artifact failed to load, using historical averages", exc_info=exc)
        return EtaPredictor(None)
    log.info("ETA model loaded", extra={"model_version": artifact["model_version"]})
    return EtaPredictor(artifact)


@lru_cache
def get_predictor() -> EtaPredictor:
    """Process-wide predictor, loaded once at startup."""
    return load_predictor(get_settings().eta_model_path)


def buffer_minutes() -> float:
    return float(get_settings().eta_buffer_minutes)


def displayed_eta(
    planning_eta: float | None,
    revised_predicted_time: float | None,
) -> float | None:
    """The single ETA shown to the operator: revised + buffer once a revision exists."""
    if revised_predicted_time is not None:
        return round(revised_predicted_time + buffer_minutes(), 1)
    return planning_eta
