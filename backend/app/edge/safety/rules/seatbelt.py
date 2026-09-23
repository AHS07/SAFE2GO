"""Seatbelt safety rule.

Type: grace period (binary signal).
The seatbelt signal is either fastened or unfastened — no hysteresis.

WARNING: unbuckled while machine can move, for >= warning_grace_seconds.
CRITICAL: still unbuckled after >= critical_grace_seconds total.

"Machine can move" means: park_brake is off AND gear is engaged,
OR machine_speed > 0.

A missing sensor value is treated as unknown — the rule does not open
an incident on unknown data, but logs a degraded state.
"""
from __future__ import annotations

from app.config.thresholds import get_thresholds
from app.edge.safety.state_machine import EvaluationResult, RuleStateMachine
from app.schemas.telemetry import TelemetryTick


def _machine_can_move(tick: TelemetryTick) -> bool:
    """Return True if the machine is capable of moving."""
    return (not tick.park_brake and tick.gear_state in ("forward", "reverse")) or tick.machine_speed > 0


def build_state_machine() -> RuleStateMachine:
    t = get_thresholds().safety.seatbelt
    return RuleStateMachine(
        debounce_seconds=0.0,
        grace_warning_seconds=float(t.warning_grace_seconds),
        grace_critical_seconds=float(t.critical_grace_seconds),
        cooldown_seconds=float(t.cooldown_seconds),
        clearing_hold_seconds=2.0,
    )


def evaluate(
    machine: RuleStateMachine,
    tick: TelemetryTick,
    sim_ts: float,
) -> EvaluationResult:
    """Evaluate one telemetry tick against the seatbelt rule."""
    # Unknown sensor value — treat as not safe, do not evaluate.
    if tick.seatbelt_status not in ("fastened", "unfastened"):
        return EvaluationResult(state=machine.state)

    condition = (
        tick.seatbelt_status == "unfastened"
        and tick.engine_running
        and _machine_can_move(tick)
    )

    return machine.evaluate(sim_ts, condition_active=condition, should_be_critical=False)
