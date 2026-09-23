"""Generic safety rule event state machine.

Every safety rule runs one instance of RuleStateMachine per machine.
The machine advances through:

    idle -> pending -> active -> clearing -> closed

All timing uses simulation-clock seconds (floats), never wall-clock time.
The caller passes the current sim timestamp on every evaluate() call.

Debounce (2 s) applies to all continuous signals before an incident opens.
Cooldown prevents re-opening immediately after an incident closes.
Hysteresis applies to continuous signals: separate trigger and clear thresholds.
Grace period applies to binary signals: WARNING after grace_warning, CRITICAL
  after grace_critical (seatbelt, operator not seated).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuleState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"      # condition present, debounce/grace running
    ACTIVE = "active"        # incident open
    CLEARING = "clearing"    # below clear threshold, confirming for 2 s
    CLOSED = "closed"        # event ended, cooldown running


@dataclass
class EvaluationResult:
    """Returned by RuleStateMachine.evaluate() on every tick."""
    state: RuleState
    # Set when state transitions to ACTIVE (open incident).
    opened: bool = False
    # Set when transitioning from ACTIVE/CLEARING to CLOSED (close incident).
    closed: bool = False
    # Set when severity should escalate to CRITICAL.
    escalate_to_critical: bool = False
    # Human-readable reason if escalate_to_critical is True.
    escalation_reason: str | None = None


class RuleStateMachine:
    """Single-rule state machine for one machine.

    Parameters
    ----------
    debounce_seconds:
        Continuous signals must hold past the trigger for this many sim-seconds
        before an event opens. Set to 0 for binary / grace-period rules.
    cooldown_seconds:
        After an event closes, the rule stays in CLOSED for this many sim-seconds
        before returning to IDLE.
    grace_warning_seconds:
        Binary rules only. WARNING opens after condition is present for this long.
    grace_critical_seconds:
        Binary rules only. Severity escalates to CRITICAL after this total duration.
    clearing_hold_seconds:
        Continuous signals must hold below the clear threshold for this many seconds
        before the event closes. Defaults to debounce_seconds.
    """

    def __init__(
        self,
        *,
        debounce_seconds: float = 2.0,
        cooldown_seconds: float = 30.0,
        grace_warning_seconds: float = 0.0,
        grace_critical_seconds: float = 0.0,
        clearing_hold_seconds: float | None = None,
    ) -> None:
        self._debounce = debounce_seconds
        self._cooldown = cooldown_seconds
        self._grace_warning = grace_warning_seconds
        self._grace_critical = grace_critical_seconds
        self._clearing_hold = clearing_hold_seconds if clearing_hold_seconds is not None else debounce_seconds

        self._state = RuleState.IDLE
        self._pending_since: float | None = None   # sim timestamp
        self._active_since: float | None = None
        self._clearing_since: float | None = None
        self._closed_since: float | None = None
        self._current_severity: str = "warning"    # "warning" or "critical"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> RuleState:
        return self._state

    @property
    def current_severity(self) -> str:
        return self._current_severity

    def evaluate(
        self,
        sim_ts: float,           # simulation timestamp as Unix epoch float
        condition_active: bool,  # is the hazard condition currently present?
        *,
        should_be_critical: bool = False,   # condition crosses CRITICAL threshold
    ) -> EvaluationResult:
        """Advance the state machine by one tick.

        sim_ts: current simulation time as seconds-since-epoch float.
        condition_active: True if any trigger threshold is met.
        should_be_critical: True if CRITICAL threshold is specifically met.
        """
        result = EvaluationResult(state=self._state)

        if self._state == RuleState.IDLE:
            result = self._from_idle(sim_ts, condition_active, should_be_critical)

        elif self._state == RuleState.PENDING:
            result = self._from_pending(sim_ts, condition_active, should_be_critical)

        elif self._state == RuleState.ACTIVE:
            result = self._from_active(sim_ts, condition_active, should_be_critical)

        elif self._state == RuleState.CLEARING:
            result = self._from_clearing(sim_ts, condition_active, should_be_critical)

        elif self._state == RuleState.CLOSED:
            result = self._from_closed(sim_ts, condition_active, should_be_critical)

        result.state = self._state
        return result

    def reset(self) -> None:
        """Return to IDLE immediately (used on simulator reset)."""
        self._state = RuleState.IDLE
        self._pending_since = None
        self._active_since = None
        self._clearing_since = None
        self._closed_since = None
        self._current_severity = "warning"

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _from_idle(self, ts: float, active: bool, critical: bool) -> EvaluationResult:
        result = EvaluationResult(state=self._state)
        if active:
            self._pending_since = ts
            self._state = RuleState.PENDING
        return result

    def _from_pending(self, ts: float, active: bool, critical: bool) -> EvaluationResult:
        result = EvaluationResult(state=self._state)
        if not active:
            # Condition cleared before debounce/grace elapsed — back to idle.
            self._state = RuleState.IDLE
            self._pending_since = None
            return result

        elapsed = ts - self._pending_since  # type: ignore[operator]

        # Grace-period rules (binary signals)
        if self._grace_warning > 0:
            if elapsed >= self._grace_critical and self._grace_critical > 0:
                self._current_severity = "critical"
                self._state = RuleState.ACTIVE
                self._active_since = ts
                result.opened = True
                result.escalation_reason = "grace_period"
            elif elapsed >= self._grace_warning:
                self._current_severity = "warning"
                self._state = RuleState.ACTIVE
                self._active_since = ts
                result.opened = True
                result.escalation_reason = "grace_period"
            return result

        # Debounce rules (continuous signals)
        if elapsed >= self._debounce:
            self._current_severity = "critical" if critical else "warning"
            self._state = RuleState.ACTIVE
            self._active_since = ts
            result.opened = True
        return result

    def _from_active(self, ts: float, active: bool, critical: bool) -> EvaluationResult:
        result = EvaluationResult(state=self._state)

        # Grace-period: escalate severity as more time elapses
        if self._grace_critical > 0 and self._pending_since is not None:
            elapsed = ts - self._pending_since
            if elapsed >= self._grace_critical and self._current_severity == "warning":
                self._current_severity = "critical"
                result.escalate_to_critical = True
                result.escalation_reason = "grace_period"

        # Continuous: escalate if CRITICAL threshold now crossed
        if critical and self._current_severity == "warning":
            self._current_severity = "critical"
            result.escalate_to_critical = True
            result.escalation_reason = "threshold"

        if not active:
            self._clearing_since = ts
            self._state = RuleState.CLEARING

        return result

    def _from_clearing(self, ts: float, active: bool, critical: bool) -> EvaluationResult:
        result = EvaluationResult(state=self._state)

        if active:
            # Re-triggered before clearing confirmed — back to active.
            self._state = RuleState.ACTIVE
            self._clearing_since = None
            return result

        elapsed = ts - self._clearing_since  # type: ignore[operator]
        if elapsed >= self._clearing_hold:
            self._state = RuleState.CLOSED
            self._closed_since = ts
            result.closed = True

        return result

    def _from_closed(self, ts: float, active: bool, critical: bool) -> EvaluationResult:
        result = EvaluationResult(state=self._state)
        elapsed = ts - self._closed_since  # type: ignore[operator]
        if elapsed >= self._cooldown:
            self._state = RuleState.IDLE
            self._pending_since = None
            self._active_since = None
            self._clearing_since = None
            self._closed_since = None
        return result
