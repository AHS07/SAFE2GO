"""Visibility (working condition) safety rule.

Type: hysteresis (continuous signal).

WARNING:  visibility < 100 m  /  clears above 120 m
CRITICAL: visibility < 50 m   /  clears above 60 m
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleState, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety
    return RuleStateMachine(
        debounce_seconds=float(t.debounce_seconds),
        cooldown_seconds=float(t.visibility.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    t = get_thresholds().safety.visibility
    vis = tick.visibility

    if machine.state in (RuleState.ACTIVE, RuleState.CLEARING):
        if machine.current_severity == "critical":
            condition = vis < t.critical_clear_meters
        else:
            condition = vis < t.warning_clear_meters
    else:
        condition = vis < t.warning_trigger_meters

    crit_active = vis < t.critical_trigger_meters

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=crit_active)
