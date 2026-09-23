"""Ground-truth work model for the batch generator.

These factors decide how fast a task really progresses in generated
telemetry. The ETA model never sees them directly. It only sees
assignment-time features and must learn their effect from history.

A task finishes when its completed quantity reaches the target, so its
duration comes out of the tick stream rather than being fixed in advance.
"""
from __future__ import annotations

from app.shared.enums import MachineType, MaterialType, SkillLevel, TaskType, WeatherCategory

# Seconds for one full load cycle at nominal conditions.
CYCLE_SECONDS: dict[MachineType, int] = {
    MachineType.EXCAVATOR: 45,
    MachineType.WHEEL_LOADER: 60,
    MachineType.BACKHOE_LOADER: 50,
}

SKILL_CYCLE_FACTOR: dict[SkillLevel, float] = {
    SkillLevel.BEGINNER: 1.25,
    SkillLevel.INTERMEDIATE: 1.0,
    SkillLevel.EXPERT: 0.85,
}

# Share of task ticks spent actively working. Travel takes a fixed share;
# the remainder is short idle moments.
SKILL_WORK_SHARE: dict[SkillLevel, float] = {
    SkillLevel.BEGINNER: 0.60,
    SkillLevel.INTERMEDIATE: 0.68,
    SkillLevel.EXPERT: 0.75,
}
TRAVEL_SHARE = 0.15

WEATHER_CYCLE_FACTOR: dict[WeatherCategory, float] = {
    WeatherCategory.CLEAR: 1.0,
    WeatherCategory.CLOUDY: 1.03,
    WeatherCategory.RAIN: 1.15,
    WeatherCategory.FOG: 1.20,
    WeatherCategory.STORM: 1.40,
}

MATERIAL_CYCLE_FACTOR: dict[MaterialType, float] = {
    MaterialType.TOPSOIL: 0.90,
    MaterialType.SANDY_SOIL: 0.95,
    MaterialType.GRAVEL: 1.0,
    MaterialType.MIXED_FILL: 1.05,
    MaterialType.CLAY: 1.10,
    MaterialType.ROCK: 1.30,
}

TASK_TYPE_CYCLE_FACTOR: dict[TaskType, float] = {
    TaskType.MATERIAL_LOADING: 0.90,
    TaskType.EXCAVATION: 1.0,
    TaskType.TRENCHING: 1.15,
}

AGE_FACTOR_PER_YEAR = 0.02
TASK_NOISE_RANGE = (0.90, 1.10)

# Bucket fill per completed cycle, in percent of rated capacity. Kept
# below the 105% overload warning so normal work does not raise incidents.
CYCLE_FILL_PCT_RANGE = (85.0, 102.0)
NOMINAL_FILL_FRACTION = 0.93

# Cycles per task, used to size target quantities so 3 to 5 tasks
# roughly fill an 8-hour shift.
TASK_CYCLES_RANGE = (35.0, 100.0)


def cycle_seconds(
    machine_type: MachineType,
    task_type: TaskType,
    skill: SkillLevel,
    weather_actual: WeatherCategory,
    material: MaterialType,
    machine_age: int,
    noise: float,
) -> int:
    """Return the true cycle length in seconds for one task."""
    seconds = (
        CYCLE_SECONDS[machine_type]
        * TASK_TYPE_CYCLE_FACTOR[task_type]
        * SKILL_CYCLE_FACTOR[skill]
        * WEATHER_CYCLE_FACTOR[weather_actual]
        * MATERIAL_CYCLE_FACTOR[material]
        * (1.0 + (machine_age - 1) * AGE_FACTOR_PER_YEAR)
        * noise
    )
    return max(1, round(seconds))
