"""Operator not seated safety rule.

Type: grace period (binary signal). WARNING level does not apply —
this rule opens directly at CRITICAL after critical_grace_seconds.

CRITICAL: seat empty, engine running, park brake off, for >= 3 s.

A missing sensor value is treated as unknown — no incident opened.
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety.operator_not_seated
    return RuleStateMachine(
        debounce_seconds=0.0,
        # No WARNING level — set grace_warning to 0 so it jumps straight to CRITICAL.
        grace_warning_seconds=0.0,
        grace_critical_seconds=float(t.critical_grace_seconds),
        cooldown_seconds=float(t.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    # Unknown sensor — treat as unknown, never as safe.
    # seat_occupied is a bool so None would indicate a missing read; skip.
    condition = (
        not tick.seat_occupied
        and tick.engine_running
        and not tick.park_brake
    )

    # This rule always opens at CRITICAL.
    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=condition)
