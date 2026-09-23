"""Overload safety rule.

Type: hysteresis (continuous signal).
Uses payload_pct — the instantaneous bucket load as a percentage of rated capacity.

WARNING:  payload_pct >= 105%  /  clears below 100%
CRITICAL: payload_pct >= 120%  /  clears below 110%
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleState, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety
    return RuleStateMachine(
        debounce_seconds=float(t.debounce_seconds),
        cooldown_seconds=float(t.overload.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    t = get_thresholds().safety.overload
    pct = tick.payload_pct

    if machine.state in (RuleState.ACTIVE, RuleState.CLEARING):
        if machine.current_severity == "critical":
            condition = pct >= t.critical_clear_pct
        else:
            condition = pct >= t.warning_clear_pct
    else:
        condition = pct >= t.warning_trigger_pct

    crit_active = pct >= t.critical_trigger_pct

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=crit_active)
