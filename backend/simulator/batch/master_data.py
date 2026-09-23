"""Master data definitions for the synthetic dataset.

Produces the fixed fleet and operator roster that every other generator
module depends on. All output is deterministic given the same seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.shared.enums import MachineStatus, MachineType, SkillLevel

# ---------------------------------------------------------------------------
# Machine specs per type
# ---------------------------------------------------------------------------

# Neutral model codes: type prefix plus rough operating weight class in tonnes.
_MACHINE_SPECS: dict[MachineType, dict[str, Any]] = {
    MachineType.EXCAVATOR: {
        "models": ["EX-20", "EX-36", "EX-90"],
        "bucket_capacity_range": (0.8, 3.5),   # m3
        "tilt_limit_degrees": 30.0,
        "rated_max_rpm": 1800.0,
        "service_interval_hours": 500.0,
    },
    MachineType.WHEEL_LOADER: {
        "models": ["WL-18", "WL-24", "WL-30"],
        "bucket_capacity_range": (2.5, 6.0),
        "tilt_limit_degrees": 15.0,
        "rated_max_rpm": 2100.0,
        "service_interval_hours": 500.0,
    },
    MachineType.BACKHOE_LOADER: {
        "models": ["BL-7", "BL-8", "BL-9"],
        "bucket_capacity_range": (0.3, 1.0),
        "tilt_limit_degrees": 25.0,
        "rated_max_rpm": 2000.0,
        "service_interval_hours": 400.0,
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MachineRecord:
    machine_id: str
    machine_model: str
    machine_type: MachineType
    machine_age: int               # years
    machine_status: MachineStatus
    service_interval_hours: float
    last_service_engine_hours: float
    bucket_capacity: float
    tilt_limit_degrees: float
    rated_max_rpm: float


@dataclass
class OperatorRecord:
    operator_id: str
    operator_name: str
    qualifications: list[tuple[MachineType, SkillLevel]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

# Fleet: 20 machines — 8 excavators, 8 wheel loaders, 4 backhoe loaders
_FLEET_COMPOSITION: list[tuple[MachineType, int]] = [
    (MachineType.EXCAVATOR, 8),
    (MachineType.WHEEL_LOADER, 8),
    (MachineType.BACKHOE_LOADER, 4),
]

_FIRST_NAMES = [
    "James", "Maria", "Chen", "Amara", "Carlos", "Priya",
    "Yusuf", "Sofia", "Dmitri", "Fatima", "Liam", "Mei",
    "Andre", "Elena", "Kofi", "Aisha", "Marco", "Zara",
    "Ravi", "Ingrid", "Omar", "Nina", "Patrick", "Layla", "Samuel",
]
_LAST_NAMES = [
    "Okonkwo", "Reyes", "Petrov", "Nguyen", "Schmidt", "Alves",
    "Hassan", "Chen", "Johansson", "Patel", "Muller", "Kim",
    "Santos", "Ivanova", "Mensah", "Park", "Costa", "Andersen",
    "Khan", "Berg", "Ali", "Moreira", "Fischer", "Yamamoto", "Osei",
]


def generate_machines(rng: np.random.Generator) -> list[MachineRecord]:
    machines: list[MachineRecord] = []
    for machine_type, count in _FLEET_COMPOSITION:
        specs = _MACHINE_SPECS[machine_type]
        for i in range(count):
            model = specs["models"][i % len(specs["models"])]
            age = int(rng.integers(1, 8))
            lo, hi = specs["bucket_capacity_range"]
            bucket = round(float(rng.uniform(lo, hi)), 2)
            last_service = round(float(rng.uniform(0, specs["service_interval_hours"] * 0.9)), 1)
            machines.append(
                MachineRecord(
                    machine_id=_rng_uuid(rng),
                    machine_model=model,
                    machine_type=machine_type,
                    machine_age=age,
                    machine_status=MachineStatus.AVAILABLE,
                    service_interval_hours=specs["service_interval_hours"],
                    last_service_engine_hours=last_service,
                    bucket_capacity=bucket,
                    tilt_limit_degrees=specs["tilt_limit_degrees"],
                    rated_max_rpm=specs["rated_max_rpm"],
                )
            )
    return machines


def _rng_uuid(rng: np.random.Generator) -> str:
    """Generate a UUID4-format string driven by the numpy RNG for reproducibility."""
    raw = rng.integers(0, 256, size=16, dtype=np.uint8)
    raw[6] = (raw[6] & 0x0F) | 0x40   # version 4
    raw[8] = (raw[8] & 0x3F) | 0x80   # variant bits
    h = raw.tobytes().hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def generate_operators(rng: np.random.Generator) -> list[OperatorRecord]:
    """Generate 25 operators. Every machine type has operators at all 3 skill levels."""
    all_types = [MachineType.EXCAVATOR, MachineType.WHEEL_LOADER, MachineType.BACKHOE_LOADER]
    skill_levels = [SkillLevel.BEGINNER, SkillLevel.INTERMEDIATE, SkillLevel.EXPERT]

    operators: list[OperatorRecord] = []
    name_indices = rng.permutation(25)

    for i in range(25):
        ni = int(name_indices[i])
        name = f"{_FIRST_NAMES[ni % len(_FIRST_NAMES)]} {_LAST_NAMES[ni % len(_LAST_NAMES)]}"
        operators.append(
            OperatorRecord(
                operator_id=_rng_uuid(rng),
                operator_name=name,
                qualifications=[],
            )
        )

    # Explicitly seed all 9 (machine_type, skill_level) combinations first
    # so the guarantee is unconditional, regardless of the random secondary
    # assignments below.
    required = [(mt, sk) for mt in all_types for sk in skill_levels]
    for idx, (mt, sk) in enumerate(required):
        operators[idx].qualifications.append((mt, sk))

    # Remaining 16 operators: assign a primary qualification
    for idx in range(len(required), len(operators)):
        primary_type = all_types[idx % len(all_types)]
        primary_skill = skill_levels[int(rng.integers(0, 3))]
        operators[idx].qualifications.append((primary_type, primary_skill))

    # ~40% of all operators get a second qualification
    for op in operators:
        if rng.random() < 0.4:
            current_types = {mt for mt, _ in op.qualifications}
            other_types = [t for t in all_types if t not in current_types]
            if other_types:
                second_type = other_types[int(rng.integers(0, len(other_types)))]
                second_skill = skill_levels[int(rng.integers(0, 3))]
                op.qualifications.append((second_type, second_skill))

    return operators
