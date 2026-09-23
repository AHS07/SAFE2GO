"""Shared simulation clock.

This is the ONLY place in the codebase that may read the system clock.
All other modules must call sim_now() or advance_clock() from here.

In live mode the clock advances with each simulator tick.
In batch mode the clock is driven by the generator.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime


class SimulationClock:
    """Thread-safe, monotonically advancing simulation clock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: datetime = datetime.now(tz=UTC)

    def now(self) -> datetime:
        """Return the current simulation time."""
        with self._lock:
            return self._current

    def set(self, ts: datetime) -> None:
        """Set the clock to a specific simulation timestamp."""
        with self._lock:
            self._current = ts

    def advance(self, seconds: float) -> datetime:
        """Advance the clock by the given number of seconds and return the new time."""
        from datetime import timedelta

        with self._lock:
            self._current = self._current + timedelta(seconds=seconds)
            return self._current


# Module-level singleton used by the entire application.
_clock = SimulationClock()


def wall_clock_now() -> datetime:
    """Real UTC time, for things that must follow real time: log timestamps
    and session token expiry. Domain logic uses sim_now()."""
    return datetime.now(tz=UTC)


def sim_now() -> datetime:
    """Return the current simulation time. Use this instead of datetime.now()."""
    return _clock.now()


def set_sim_time(ts: datetime) -> None:
    """Set the simulation clock. Called by the simulator on each tick."""
    _clock.set(ts)


def advance_sim_time(seconds: float) -> datetime:
    """Advance the simulation clock. Called by the clock driver."""
    return _clock.advance(seconds)
