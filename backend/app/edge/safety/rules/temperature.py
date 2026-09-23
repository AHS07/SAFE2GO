"""Ambient temperature (working condition) safety rule.

Type: hysteresis (continuous signal).

WARNING:  ambient_temp >= 35 C  /  clears below 33 C
CRITICAL: ambient_temp >= 40 C  /  clears below 38 C
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleState, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety
    return RuleStateMachine(
        debounce_seconds=float(t.debounce_seconds),
        cooldown_seconds=float(t.ambient_temperature.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    t = get_thresholds().safety.ambient_temperature
    temp = tick.ambient_temp

    if machine.state in (RuleState.ACTIVE, RuleState.CLEARING):
        if machine.current_severity == "critical":
            condition = temp >= t.critical_clear_celsius
        else:
            condition = temp >= t.warning_clear_celsius
    else:
        condition = temp >= t.warning_trigger_celsius

    crit_active = temp >= t.critical_trigger_celsius

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=crit_active)
