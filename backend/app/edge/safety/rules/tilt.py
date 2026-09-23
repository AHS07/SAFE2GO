"""Tilt safety rule.

Type: hysteresis (continuous signal).
Thresholds are expressed as a fraction of the machine's tilt_limit_degrees,
which varies per machine type and is provided at runtime.

WARNING:  tilt_angle >= 80% of limit  /  clears below 75%
CRITICAL: tilt_angle >= 100% of limit /  clears below 95%
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleState, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety
    return RuleStateMachine(
        debounce_seconds=float(t.debounce_seconds),
        cooldown_seconds=float(t.tilt.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
    *,
    tilt_limit_degrees: float,
) -> EvaluationResult:
    t = get_thresholds().safety.tilt
    angle = tick.tilt_angle

    warn_trigger = tilt_limit_degrees * t.warning_trigger_fraction
    warn_clear   = tilt_limit_degrees * t.warning_clear_fraction
    crit_trigger = tilt_limit_degrees * t.critical_trigger_fraction
    crit_clear   = tilt_limit_degrees * t.critical_clear_fraction

    if machine.state in (RuleState.ACTIVE, RuleState.CLEARING):
        if machine.current_severity == "critical":
            condition = angle >= crit_clear
        else:
            condition = angle >= warn_clear
    else:
        condition = angle >= warn_trigger

    crit_active = angle >= crit_trigger

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=crit_active)
