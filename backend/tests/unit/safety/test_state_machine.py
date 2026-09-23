"""Unit tests for the generic safety rule state machine."""
from __future__ import annotations

from app.edge.safety.state_machine import RuleState, RuleStateMachine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sm_continuous(debounce: float = 2.0, cooldown: float = 30.0) -> RuleStateMachine:
    return RuleStateMachine(debounce_seconds=debounce, cooldown_seconds=cooldown)


def sm_grace(warn: float = 10.0, crit: float = 30.0, cooldown: float = 60.0) -> RuleStateMachine:
    return RuleStateMachine(
        debounce_seconds=0.0,
        grace_warning_seconds=warn,
        grace_critical_seconds=crit,
        cooldown_seconds=cooldown,
    )


def advance(sm: RuleStateMachine, ts: float, active: bool, critical: bool = False):
    return sm.evaluate(ts, condition_active=active, should_be_critical=critical)


# ---------------------------------------------------------------------------
# Continuous (debounce) rules
# ---------------------------------------------------------------------------


def test_idle_no_condition():
    sm = sm_continuous()
    r = advance(sm, 0.0, False)
    assert r.state == RuleState.IDLE
    assert not r.opened


def test_condition_starts_pending():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    assert sm.state == RuleState.PENDING


def test_debounce_not_elapsed_stays_pending():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    r = advance(sm, 1.0, True)
    assert r.state == RuleState.PENDING
    assert not r.opened


def test_debounce_elapsed_opens_incident():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    r = advance(sm, 2.0, True)
    assert r.opened
    assert r.state == RuleState.ACTIVE


def test_condition_clears_before_debounce_returns_idle():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    advance(sm, 1.0, False)
    assert sm.state == RuleState.IDLE


def test_active_condition_clears_enters_clearing():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)   # opens
    advance(sm, 3.0, False)  # condition gone
    assert sm.state == RuleState.CLEARING


def test_clearing_hold_closes_incident():
    sm = sm_continuous(debounce=2.0, cooldown=30.0)
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)   # opens
    advance(sm, 3.0, False)  # clearing starts
    r = advance(sm, 5.0, False)  # 2 s of clearing hold
    assert r.closed
    assert r.state == RuleState.CLOSED


def test_retrigger_during_clearing_goes_back_to_active():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)   # opens
    advance(sm, 3.0, False)  # clearing
    advance(sm, 4.0, True)   # re-triggered
    assert sm.state == RuleState.ACTIVE


def test_cooldown_prevents_immediate_reopen():
    sm = sm_continuous(debounce=2.0, cooldown=30.0)
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)
    advance(sm, 3.0, False)
    advance(sm, 5.0, False)  # closed
    # Immediately try to trigger again
    advance(sm, 6.0, True)
    assert sm.state == RuleState.CLOSED  # still in cooldown, not pending


def test_cooldown_expires_returns_to_idle():
    sm = sm_continuous(debounce=2.0, cooldown=30.0)
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)
    advance(sm, 3.0, False)
    advance(sm, 5.0, False)  # closed at t=5
    advance(sm, 36.0, False)  # cooldown expired (30 s)
    assert sm.state == RuleState.IDLE


def test_escalates_to_critical_when_threshold_crossed():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True, critical=False)
    r = advance(sm, 2.0, True, critical=False)
    assert r.opened
    assert sm.current_severity == "warning"

    r2 = advance(sm, 3.0, True, critical=True)
    assert r2.escalate_to_critical
    assert sm.current_severity == "critical"


def test_opens_directly_as_critical_when_threshold_met_at_open():
    sm = sm_continuous(debounce=2.0)
    advance(sm, 0.0, True, critical=True)
    r = advance(sm, 2.0, True, critical=True)
    assert r.opened
    assert sm.current_severity == "critical"


def test_reset_returns_to_idle():
    sm = sm_continuous()
    advance(sm, 0.0, True)
    advance(sm, 2.0, True)
    sm.reset()
    assert sm.state == RuleState.IDLE


# ---------------------------------------------------------------------------
# Grace-period rules
# ---------------------------------------------------------------------------


def test_grace_warning_opens_after_elapsed():
    sm = sm_grace(warn=10.0, crit=30.0)
    advance(sm, 0.0, True)
    r = advance(sm, 10.0, True)
    assert r.opened
    assert sm.current_severity == "warning"


def test_grace_critical_escalates_after_elapsed():
    sm = sm_grace(warn=10.0, crit=30.0)
    advance(sm, 0.0, True)
    advance(sm, 10.0, True)  # opens as WARNING
    r = advance(sm, 30.0, True)
    assert r.escalate_to_critical
    assert sm.current_severity == "critical"


def test_grace_clears_before_warning():
    sm = sm_grace(warn=10.0, crit=30.0)
    advance(sm, 0.0, True)
    advance(sm, 5.0, False)
    assert sm.state == RuleState.IDLE
