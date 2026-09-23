"""Proximity safety rule.

Type: hysteresis (continuous signal).

WARNING:  proximity_distance < warning_trigger_meters  /  clears above warning_clear_meters
CRITICAL: proximity_distance < critical_trigger_meters /  clears above critical_clear_meters

proximity_distance = None means no object detected in sensor range — treated
as safe (None is explicitly "no reading", not a missing sensor).
A truly missing/malformed tick would have been rejected by the ingest layer.
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleState, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety
    return RuleStateMachine(
        debounce_seconds=float(t.debounce_seconds),
        cooldown_seconds=float(t.proximity.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    t = get_thresholds().safety.proximity

    # None = no object in range = safe.
    if tick.proximity_distance is None:
        return machine.evaluate(sim_ts, condition_active=False)

    dist = tick.proximity_distance

    # Hysteresis: once active, use clear thresholds; otherwise use trigger thresholds.
    if machine.state in (RuleState.ACTIVE, RuleState.CLEARING):
        # Use clear thresholds
        warn_active = dist < t.warning_trigger_meters and dist >= t.critical_trigger_meters
        crit_active = dist < t.critical_trigger_meters
        # Clear when above respective clear value
        if machine.current_severity == "critical":
            condition = dist < t.critical_clear_meters
        else:
            condition = dist < t.warning_clear_meters
    else:
        warn_active = dist < t.warning_trigger_meters
        crit_active = dist < t.critical_trigger_meters
        condition = warn_active or crit_active

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=crit_active)
