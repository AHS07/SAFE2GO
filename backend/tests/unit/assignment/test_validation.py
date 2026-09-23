"""Assignment validation rules: an allowed and a rejected case for each (rules.md section 6)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.cloud.assignment.validation import (
    SHIFT_END_EXCEEDED,
    Slot,
    require_available,
    require_compatible,
    require_no_shift_overlap,
    require_no_task_overlap,
    require_qualification,
    require_start_inside_shift,
    require_unit_for_task,
    require_valid_window,
    shift_end_warning,
)
from app.cloud.eta.service import apply_assignment_estimate
from app.core.errors import (
    MachineInMaintenanceError,
    OperatorNotQualifiedError,
    ShiftOverlapError,
    TaskMachineIncompatibleError,
    TaskOverlapError,
    ValidationError,
)
from app.db.models.cloud.task import Task
from app.shared.eta_model import EtaEstimate

T0 = datetime(2030, 1, 7, 7, 0, tzinfo=UTC)
H = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------


def test_qualified_operator_returns_skill() -> None:
    assert require_qualification({"excavator": "expert"}, "excavator", "op-1") == "expert"


def test_unqualified_operator_rejected() -> None:
    with pytest.raises(OperatorNotQualifiedError) as err:
        require_qualification({"wheel_loader": "expert"}, "excavator", "op-1")
    assert err.value.details["qualified_types"] == ["wheel_loader"]


# ---------------------------------------------------------------------------
# Task-machine compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_type", "machine_type"),
    [
        ("excavation", "excavator"),
        ("excavation", "backhoe_loader"),
        ("material_loading", "wheel_loader"),
        ("trenching", "backhoe_loader"),
    ],
)
def test_compatible_task_allowed(task_type: str, machine_type: str) -> None:
    require_compatible(task_type, machine_type, "m-1")


@pytest.mark.parametrize("task_type", ["excavation", "trenching"])
def test_incompatible_task_rejected(task_type: str) -> None:
    with pytest.raises(TaskMachineIncompatibleError):
        require_compatible(task_type, "wheel_loader", "m-1")


# ---------------------------------------------------------------------------
# Quantity unit per task type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_type", "unit"),
    [("excavation", "m3"), ("trenching", "m3"), ("material_loading", "loads")],
)
def test_matching_unit_allowed(task_type: str, unit: str) -> None:
    require_unit_for_task(task_type, unit)


@pytest.mark.parametrize(
    ("task_type", "unit"),
    [("excavation", "loads"), ("material_loading", "m3")],
)
def test_mismatched_unit_rejected(task_type: str, unit: str) -> None:
    with pytest.raises(ValidationError) as err:
        require_unit_for_task(task_type, unit)
    assert err.value.details["quantity_unit"] == unit


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


def test_available_machine_allowed() -> None:
    require_available("available", "m-1")


def test_machine_in_maintenance_rejected() -> None:
    with pytest.raises(MachineInMaintenanceError):
        require_available("maintenance", "m-1")


# ---------------------------------------------------------------------------
# Shift window and shift overlap
# ---------------------------------------------------------------------------


def test_valid_shift_window_allowed() -> None:
    require_valid_window(T0, T0 + 8 * H)


@pytest.mark.parametrize("end", [T0, T0 - H])
def test_empty_or_reversed_shift_window_rejected(end: datetime) -> None:
    with pytest.raises(ValidationError):
        require_valid_window(T0, end)


def test_back_to_back_shifts_allowed() -> None:
    earlier = Slot("sh-1", T0 - 8 * H, T0)
    require_no_shift_overlap(T0, T0 + 8 * H, "op-1", "m-1", [earlier], [earlier])


def test_operator_double_booked_rejected() -> None:
    existing = Slot("sh-1", T0 + 4 * H, T0 + 12 * H)
    with pytest.raises(ShiftOverlapError) as err:
        require_no_shift_overlap(T0, T0 + 8 * H, "op-1", "m-1", [existing], [])
    assert err.value.details == {"conflicting_shift_id": "sh-1", "conflict_on": "operator"}


def test_machine_double_booked_rejected() -> None:
    existing = Slot("sh-2", T0 - 2 * H, T0 + H)
    with pytest.raises(ShiftOverlapError) as err:
        require_no_shift_overlap(T0, T0 + 8 * H, "op-1", "m-1", [], [existing])
    assert err.value.details["conflict_on"] == "machine"


def test_shift_inside_existing_shift_rejected() -> None:
    existing = Slot("sh-3", T0 - H, T0 + 10 * H)
    with pytest.raises(ShiftOverlapError):
        require_no_shift_overlap(T0, T0 + 8 * H, "op-1", "m-1", [existing], [])


# ---------------------------------------------------------------------------
# Task start inside its shift
# ---------------------------------------------------------------------------

SHIFT = Slot("sh-1", T0, T0 + 8 * H)


@pytest.mark.parametrize("start", [T0, T0 + 4 * H, T0 + 8 * H - timedelta(minutes=1)])
def test_task_start_inside_shift_allowed(start: datetime) -> None:
    require_start_inside_shift(start, SHIFT)


@pytest.mark.parametrize("start", [T0 - timedelta(minutes=1), T0 + 8 * H, T0 + 9 * H])
def test_task_start_outside_shift_rejected(start: datetime) -> None:
    with pytest.raises(ValidationError):
        require_start_inside_shift(start, SHIFT)


# ---------------------------------------------------------------------------
# Task overlap within a shift
# ---------------------------------------------------------------------------


def test_back_to_back_tasks_allowed() -> None:
    first = Slot("t-1", T0, T0 + H)
    require_no_task_overlap(T0 + H, T0 + 2 * H, [first])


def test_overlapping_task_rejected() -> None:
    first = Slot("t-1", T0, T0 + 2 * H)
    with pytest.raises(TaskOverlapError) as err:
        require_no_task_overlap(T0 + H, T0 + 3 * H, [first])
    assert err.value.details["conflicting_task_id"] == "t-1"


def test_task_ending_inside_next_task_rejected() -> None:
    later = Slot("t-2", T0 + 2 * H, T0 + 3 * H)
    with pytest.raises(TaskOverlapError):
        require_no_task_overlap(T0 + H, T0 + 2 * H + timedelta(minutes=5), [later])


# ---------------------------------------------------------------------------
# Shift end warning
# ---------------------------------------------------------------------------


def test_task_ending_by_shift_end_has_no_warning() -> None:
    assert shift_end_warning(T0 + 8 * H, T0 + 8 * H) is None


def test_task_past_shift_end_warns_without_blocking() -> None:
    warning = shift_end_warning(T0 + 8 * H + timedelta(minutes=25), T0 + 8 * H)
    assert warning is not None
    assert warning.code == SHIFT_END_EXCEEDED
    assert warning.details["overrun_minutes"] == 25.0


# ---------------------------------------------------------------------------
# ETA on reassignment
# ---------------------------------------------------------------------------


def _assigned_task() -> Task:
    task = Task(task_id="t-1", scheduled_start=T0)
    apply_assignment_estimate(task, EtaEstimate(60.0, 55.0, False, "rf-1"), "agg-1")
    return task


def test_reassignment_before_start_recomputes_raw_estimate() -> None:
    task = _assigned_task()
    apply_assignment_estimate(task, EtaEstimate(90.0, 80.0, False, "rf-1"), "agg-1", reassignment=True)
    assert task.raw_predicted_time == 90.0
    assert task.scheduled_end == T0 + timedelta(minutes=task.planning_eta)


def test_reassignment_after_start_keeps_raw_estimate() -> None:
    task = _assigned_task()
    task.actual_start = T0
    with pytest.raises(ValidationError):
        apply_assignment_estimate(task, EtaEstimate(90.0, 80.0, False, "rf-1"), "agg-1", reassignment=True)
    assert task.raw_predicted_time == 60.0
