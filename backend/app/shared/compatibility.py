"""Task-machine compatibility matrix (architecture.md section 5) and the
quantity unit each task type is measured in.

Shared by the cloud assignment validation and the batch generator so both
use one definition. The ETA model is trained on exactly these unit pairs.
"""
from __future__ import annotations

from app.shared.enums import MachineType, QuantityUnit, TaskType

TASK_MACHINE_COMPAT: dict[TaskType, frozenset[MachineType]] = {
    TaskType.EXCAVATION: frozenset({MachineType.EXCAVATOR, MachineType.BACKHOE_LOADER}),
    TaskType.MATERIAL_LOADING: frozenset(
        {MachineType.EXCAVATOR, MachineType.WHEEL_LOADER, MachineType.BACKHOE_LOADER}
    ),
    TaskType.TRENCHING: frozenset({MachineType.EXCAVATOR, MachineType.BACKHOE_LOADER}),
}

TASK_UNIT: dict[TaskType, QuantityUnit] = {
    TaskType.EXCAVATION: QuantityUnit.M3,
    TaskType.MATERIAL_LOADING: QuantityUnit.LOADS,
    TaskType.TRENCHING: QuantityUnit.M3,
}


def is_compatible(task_type: str, machine_type: str) -> bool:
    return MachineType(machine_type) in TASK_MACHINE_COMPAT[TaskType(task_type)]


def compatible_task_types(machine_type: MachineType) -> list[TaskType]:
    return [tt for tt, mtypes in TASK_MACHINE_COMPAT.items() if machine_type in mtypes]
