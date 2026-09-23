"""Phase 10: idle ratio threshold tuning rule and detector override."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.edge.behavior.detectors.idle_ratio import IdleRatioDetector
from evaluation.tune_idle_ratio import MAX_MULTIPLIER, Score, choose

T0 = datetime(2030, 1, 7, 7, 0, tzinfo=UTC)


def test_choice_keeps_every_training_anomaly_then_fewest_false_positives() -> None:
    scores = [Score(3.0, 5, 5, 40), Score(4.0, 5, 5, 20), Score(5.0, 4, 5, 2), Score(MAX_MULTIPLIER, 5, 5, 20)]
    assert choose(scores).multiplier == 4.0


def test_choice_never_goes_above_the_cap() -> None:
    scores = [Score(MAX_MULTIPLIER, 5, 5, 7), Score(MAX_MULTIPLIER + 4, 5, 5, 0)]
    assert choose(scores).multiplier == MAX_MULTIPLIER


def test_detector_uses_the_override_multiplier() -> None:
    detector = IdleRatioDetector()
    args = {"shift_start": T0, "shift_end": T0 + timedelta(hours=8), "idle_ratio": 0.5,
            "baseline_median": 0.2, "baseline_mad": 0.05}
    assert detector.check(**args, mad_multiplier=3.0) is not None     # threshold 0.35
    assert detector.check(**args, mad_multiplier=7.0) is None         # threshold 0.55


def test_settings_refuse_a_weak_secret_without_echoing_it() -> None:
    import pytest
    from pydantic import ValidationError

    from app.config.settings import MIN_SECRET_LENGTH, Settings

    strong = "s" * MIN_SECRET_LENGTH
    with pytest.raises(ValidationError) as err:
        Settings(jwt_secret="too-short-secret", credential_signing_key=strong)
    assert "JWT_SECRET must be set" in str(err.value)
    assert "too-short-secret" not in str(err.value)
    assert Settings(jwt_secret=strong, credential_signing_key=strong).jwt_secret == strong
