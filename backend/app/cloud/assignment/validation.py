"""Assignment validation rules (PRD F7.3).

Pure functions over plain values so each rule can be tested without a
database. A rule either returns quietly, raises its domain error, or (for
the shift-end check) returns a warning that does not block the assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.errors import (
    MachineInMaintenanceError,
    OperatorNotQualifiedError,
    ShiftOverlapError,
    TaskMachineIncompatibleError,
    TaskOverlapError,
    ValidationError,
)
from app.shared.compatibility import TASK_UNIT, is_compatible
from app.shared.enums import MachineStatus, TaskType

SHIFT_END_EXCEEDED = "SHIFT_END_EXCEEDED"
NO_OFFLINE_SIGN_IN = "NO_OFFLINE_SIGN_IN"


@dataclass(frozen=True)
class Slot:
    """A booked time window, [start, end)."""

    entity_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class AssignmentWarning:
    code: str
    message: str
    details: dict


def _overlaps(a_start: datetime, a_end: datetime, b: Slot) -> bool:
    return a_start < b.end and b.start < a_end


def _hhmm(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def require_valid_window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValidationError(
            "Shift end must be after shift start.",
            details={"scheduled_start": start.isoformat(), "scheduled_end": end.isoformat()},
        )


def require_qualification(
    qualifications: dict[str, str], machine_type: str, operator_id: str
) -> str:
    """Return the operator's skill level on the machine type."""
    skill = qualifications.get(machine_type)
    if skill is None:
        raise OperatorNotQualifiedError(
            f"Operator {operator_id} is not qualified on {machine_type.replace('_', ' ')} machines.",
            details={
                "operator_id": operator_id,
                "machine_type": machine_type,
                "qualified_types": sorted(qualifications),
            },
        )
    return skill


def require_compatible(task_type: str, machine_type: str, machine_id: str) -> None:
    if not is_compatible(task_type, machine_type):
        raise TaskMachineIncompatibleError(
            f"A {machine_type.replace('_', ' ')} cannot do {task_type.replace('_', ' ')} tasks.",
            details={"task_type": task_type, "machine_type": machine_type, "machine_id": machine_id},
        )


def require_unit_for_task(task_type: str, quantity_unit: str) -> None:
    expected = TASK_UNIT[TaskType(task_type)]
    if quantity_unit != expected.value:
        raise ValidationError(
            f"{task_type.replace('_', ' ').capitalize()} is measured in {expected.value}.",
            details={"task_type": task_type, "quantity_unit": quantity_unit, "expected_unit": expected.value},
        )


def require_available(machine_status: str, machine_id: str) -> None:
    if machine_status == MachineStatus.MAINTENANCE.value:
        raise MachineInMaintenanceError(
            f"Machine {machine_id} is in maintenance.",
            details={"machine_id": machine_id},
        )


def require_no_shift_overlap(
    start: datetime,
    end: datetime,
    operator_id: str,
    machine_id: str,
    operator_shifts: list[Slot],
    machine_shifts: list[Slot],
) -> None:
    """Neither the operator nor the machine may already be booked in the window."""
    for slot in operator_shifts:
        if _overlaps(start, end, slot):
            raise ShiftOverlapError(
                f"Operator {operator_id} already has a shift from {_hhmm(slot.start)} to {_hhmm(slot.end)}.",
                details={"conflicting_shift_id": slot.entity_id, "conflict_on": "operator"},
            )
    for slot in machine_shifts:
        if _overlaps(start, end, slot):
            raise ShiftOverlapError(
                f"Machine {machine_id} already has a shift from {_hhmm(slot.start)} to {_hhmm(slot.end)}.",
                details={"conflicting_shift_id": slot.entity_id, "conflict_on": "machine"},
            )


def require_start_inside_shift(task_start: datetime, shift: Slot) -> None:
    if not shift.start <= task_start < shift.end:
        raise ValidationError(
            f"Task start must be within the shift, {_hhmm(shift.start)} to {_hhmm(shift.end)}.",
            details={
                "shift_id": shift.entity_id,
                "scheduled_start": task_start.isoformat(),
                "shift_start": shift.start.isoformat(),
                "shift_end": shift.end.isoformat(),
            },
        )


def require_no_task_overlap(start: datetime, end: datetime, shift_tasks: list[Slot]) -> None:
    """Task windows are start to start + planning ETA and must not overlap."""
    for slot in shift_tasks:
        if _overlaps(start, end, slot):
            raise TaskOverlapError(
                f"Task overlaps task {slot.entity_id}, planned {_hhmm(slot.start)} to {_hhmm(slot.end)}.",
                details={
                    "conflicting_task_id": slot.entity_id,
                    "conflicting_start": slot.start.isoformat(),
                    "conflicting_end": slot.end.isoformat(),
                },
            )


def shift_end_warning(task_end: datetime, shift_end: datetime) -> AssignmentWarning | None:
    """A task planned to finish after the shift ends is allowed, with a warning."""
    if task_end <= shift_end:
        return None
    overrun = round((task_end - shift_end).total_seconds() / 60.0, 1)
    return AssignmentWarning(
        code=SHIFT_END_EXCEEDED,
        message=f"Planned finish is {overrun:g} min after the shift ends.",
        details={"overrun_minutes": overrun, "shift_end": shift_end.isoformat()},
    )


def sign_in_warning(operator_id: str, has_account: bool) -> AssignmentWarning:
    """A shift whose operator has no PIN gets no offline credential. Allowed, with a warning."""
    if has_account:
        message = "The operator has no PIN, so they cannot sign in on the machine while the cloud link is down."
    else:
        message = (
            "The operator has no sign-in account, so they cannot see this shift on the machine. "
            "Create one under Operators and machines."
        )
    return AssignmentWarning(
        code=NO_OFFLINE_SIGN_IN,
        message=message,
        details={"operator_id": operator_id, "has_account": has_account},
    )
