"""Simulator control routes. Available in dev/demo only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.ws.manager import ws_manager
from app.config.settings import Settings, get_settings
from app.core.clock import sim_now
from app.core.errors import NotFoundError
from app.db.session import _session_factory
from simulator.live.clock_driver import ClockDriver
from simulator.live.runner import LiveRunner
from simulator.live.scenarios import scenario_injector

router = APIRouter(prefix="/api/sim", tags=["simulator"])

_MAX_SPEED = 60

_settings = get_settings()
_clock_driver = ClockDriver(
    tick_seconds=float(_settings.sim_tick_seconds), selected_speed=_settings.sim_speed
)
_runner = LiveRunner(
    clock=_clock_driver,
    session_factory=_session_factory,
    broadcast=ws_manager.broadcast,
    seed=_settings.sim_seed,
)


def _require_demo(settings: Settings = Depends(get_settings)) -> None:
    if not settings.demo_routes_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


class StartRequest(BaseModel):
    # Machines to stream. Empty means every machine with a demo operator shift.
    machine_ids: list[str] | None = None


class SpeedRequest(BaseModel):
    speed: int = Field(ge=1, le=_MAX_SPEED)


class SpeedResponse(BaseModel):
    selected_speed: int
    effective_speed: int


class ActiveScenario(BaseModel):
    scenario: str
    machine_id: str | None
    end: str


class SimStatusResponse(BaseModel):
    running: bool
    sim_time: str
    selected_speed: int
    effective_speed: int        # 1 while any incident is open (auto-slow)
    machine_ids: list[str]
    scenarios: list[ActiveScenario]


@router.post("/start", response_model=SimStatusResponse, dependencies=[Depends(_require_demo)])
async def sim_start(body: StartRequest | None = None) -> SimStatusResponse:
    await _runner.start(body.machine_ids if body else None)
    return _sim_status()


@router.post("/stop", response_model=SimStatusResponse, dependencies=[Depends(_require_demo)])
async def sim_stop() -> SimStatusResponse:
    await _runner.stop()
    scenario_injector.clear()
    return _sim_status()


@router.get("/status", response_model=SimStatusResponse, dependencies=[Depends(_require_demo)])
async def sim_status() -> SimStatusResponse:
    return _sim_status()


@router.post("/speed", response_model=SpeedResponse, dependencies=[Depends(_require_demo)])
async def set_speed(body: SpeedRequest) -> SpeedResponse:
    _clock_driver.set_speed(body.speed)
    return SpeedResponse(
        selected_speed=_clock_driver._selected_speed,
        effective_speed=_clock_driver.effective_speed,
    )


_SCENARIOS = {
    "proximity": scenario_injector.proximity_breach,
    "overload": scenario_injector.overload,
    "seatbelt": scenario_injector.seatbelt_removal,
    "idling": scenario_injector.excessive_idling,
    "tilt": scenario_injector.tilt,
}


# Not a sensor override: runs the end-of-shift behavior check.
_END_SHIFT = "end_shift"


class ScenarioResponse(BaseModel):
    scenario: str
    start: str
    end: str
    machine_id: str | None


@router.post(
    "/scenarios/{name}", response_model=ScenarioResponse, dependencies=[Depends(_require_demo)]
)
async def inject_scenario(
    name: str,
    machine_id: str | None = Query(None, description="Target machine. Empty means every streamed machine."),
) -> ScenarioResponse:
    if name == _END_SHIFT:
        return await _end_shift(machine_id)
    if name not in _SCENARIOS:
        raise NotFoundError(f"Unknown scenario '{name}'.", details={"known": sorted([*_SCENARIOS, _END_SHIFT])})
    window = _SCENARIOS[name](machine_id=machine_id)
    return ScenarioResponse(
        scenario=window.name,
        start=window.start.isoformat(),
        end=window.end.isoformat(),
        machine_id=window.machine_id,
    )


async def _end_shift(machine_id: str | None) -> ScenarioResponse:
    """Run the end-of-shift check (idle ratio) now, once per shift.

    The live demo has no real shift end, so the presenter triggers it.
    """
    now = sim_now()
    targets = [machine_id] if machine_id else _runner.machine_ids
    ran = [mid for mid in targets if await _runner.end_shift(mid, now)]
    if not ran:
        raise NotFoundError(
            "No streamed machine with live data to end the shift for. Start the simulator first.",
            details={"machine_id": machine_id},
        )
    return ScenarioResponse(
        scenario=_END_SHIFT, start=now.isoformat(), end=now.isoformat(), machine_id=machine_id
    )


def _sim_status() -> SimStatusResponse:
    now = sim_now()
    return SimStatusResponse(
        running=_runner.is_running,
        sim_time=now.isoformat(),
        selected_speed=_clock_driver._selected_speed,
        effective_speed=_clock_driver.effective_speed,
        machine_ids=_runner.machine_ids,
        scenarios=[
            ActiveScenario(scenario=w.name, machine_id=w.machine_id, end=w.end.isoformat())
            for w in scenario_injector.active_windows(now)
        ],
    )


def get_clock_driver() -> ClockDriver:
    return _clock_driver


async def shutdown_simulator() -> None:
    await _runner.stop()
