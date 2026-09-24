"""Phase 6 unit tests: training content, quiz grading, training status,
parked check, manual retrieval, emergency guidance, and live cycle
generation. No database needed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import MachineNotParkedError, ValidationError
from app.edge.assistant.retrieval import ManualIndex, get_manual_index, split_sections
from app.edge.tasks.service import cycle_quantity
from app.edge.telemetry import state
from app.edge.training.access import is_parked, require_parked
from app.edge.training.service import TrainingStatus, derive_status, grade_quiz
from app.schemas.telemetry import TelemetryTick
from app.shared.content import (
    ManualEntry,
    Quiz,
    load_catalog,
    load_emergency_guide,
    load_quiz,
    read_text,
)
from app.shared.enums import BehaviorEventType, IncidentType, QuantityUnit
from simulator.live.stream import LiveStream, _cycle_seconds_for

PASS_PCT = 80.0
T0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)


def _tick(**overrides) -> TelemetryTick:
    raw = {
        "timestamp": T0,
        "machine_id": "M-1",
        "operator_id": "OP-1",
        "task_id": None,
        "shift_id": "SH-1",
        "engine_running": True,
        "engine_rpm": 700.0,
        "engine_hours": 100.0,
        "fuel_used": 1.0,
        "hydraulic_active": False,
        "machine_speed": 0.0,
        "load_cycles": 0,
        "payload_pct": 0.0,
        "seatbelt_status": "fastened",
        "seat_occupied": True,
        "park_brake": True,
        "gear_state": "park",
        "ambient_temp": 25.0,
        "visibility": 400.0,
        "tilt_angle": 1.0,
        "proximity_sensor_ok": True,
    }
    raw.update(overrides)
    return TelemetryTick.model_validate(raw)


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class TestContent:
    def test_catalog_has_five_modules(self):
        assert len(load_catalog().modules) == 5

    def test_every_module_has_a_valid_quiz_and_body(self):
        for module in load_catalog().modules:
            quiz = load_quiz(module.content_path)
            assert quiz.module_id == module.module_id
            assert read_text(module.content_path).startswith("# ")

    def test_map_uses_real_incident_and_behavior_types(self):
        incident_types = {t.value for t in IncidentType}
        behavior_types = {t.value for t in BehaviorEventType}
        for entry in load_catalog().recommendation_map:
            valid = incident_types if entry.source_type == "incident" else behavior_types
            assert entry.source_type in ("incident", "behavior")
            assert entry.type_value in valid

    def test_every_type_except_manual_report_is_mapped(self):
        mapped = {(e.source_type, e.type_value) for e in load_catalog().recommendation_map}
        for t in IncidentType:
            if t is not IncidentType.MANUAL_REPORT:
                assert ("incident", t.value) in mapped
        for t in BehaviorEventType:
            assert ("behavior", t.value) in mapped

    def test_proximity_and_idling_map_to_the_right_modules(self):
        by_type = {e.type_value: e.module_id for e in load_catalog().recommendation_map}
        assert by_type[IncidentType.PROXIMITY.value] == "working-near-people"
        assert by_type[BehaviorEventType.EXCESSIVE_IDLING.value] == "fuel-efficient-operation"

    def test_content_path_cannot_escape_content_dir(self):
        with pytest.raises(ValueError):
            read_text("../app/config/thresholds.yaml")

    def test_emergency_guide_covers_prd_emergencies(self):
        ids = {item.emergency_id for item in load_emergency_guide().items}
        assert {"rollover", "fire", "contact_with_person", "hydraulic_failure", "medical"} <= ids

    def test_quiz_rejects_out_of_range_answer(self):
        with pytest.raises(ValueError):
            Quiz.model_validate({
                "module_id": "x",
                "questions": [{"question": "q", "options": ["a", "b"], "answer": 2, "explanation": "e"}],
            })


# ---------------------------------------------------------------------------
# Quiz grading and training status
# ---------------------------------------------------------------------------


def _quiz(n: int = 5) -> Quiz:
    return Quiz.model_validate({
        "module_id": "m",
        "questions": [
            {"question": f"q{i}", "options": ["a", "b", "c"], "answer": 1, "explanation": "e"}
            for i in range(n)
        ],
    })


class TestQuizGrading:
    def test_all_correct_scores_100_and_passes(self):
        grade = grade_quiz(_quiz(), [1] * 5, PASS_PCT)
        assert grade.score == 100.0
        assert grade.passed

    def test_score_at_pass_mark_passes(self):
        grade = grade_quiz(_quiz(), [1, 1, 1, 1, 0], PASS_PCT)
        assert grade.score == 80.0
        assert grade.passed

    def test_score_below_pass_mark_fails(self):
        grade = grade_quiz(_quiz(), [1, 1, 1, 0, 0], PASS_PCT)
        assert grade.score == 60.0
        assert not grade.passed

    def test_per_question_feedback(self):
        grade = grade_quiz(_quiz(2), [1, 0], PASS_PCT)
        assert [q.correct for q in grade.questions] == [True, False]
        assert all(q.correct_option == 1 for q in grade.questions)

    def test_wrong_answer_count_is_rejected(self):
        with pytest.raises(ValidationError):
            grade_quiz(_quiz(), [1, 1], PASS_PCT)


class TestTrainingStatus:
    def test_no_attempts_is_not_started(self):
        assert derive_status([], PASS_PCT) is TrainingStatus.NOT_STARTED

    def test_failed_attempts_need_retry(self):
        assert derive_status([40.0, 60.0], PASS_PCT) is TrainingStatus.NEEDS_RETRY

    def test_best_attempt_decides(self):
        # A later lower score does not undo an earlier pass.
        assert derive_status([80.0, 20.0], PASS_PCT) is TrainingStatus.PASSED


# ---------------------------------------------------------------------------
# Parked check
# ---------------------------------------------------------------------------


class TestParkedCheck:
    def teardown_method(self):
        state.clear()

    def test_no_telemetry_is_not_parked(self):
        assert not is_parked(None)

    def test_brake_on_and_stationary_is_parked(self):
        assert is_parked(_tick())

    def test_moving_is_not_parked(self):
        assert not is_parked(_tick(machine_speed=4.0))

    def test_brake_off_is_not_parked(self):
        assert not is_parked(_tick(park_brake=False, gear_state="forward"))

    def test_require_parked_blocks_unknown_state(self):
        with pytest.raises(MachineNotParkedError) as exc:
            require_parked("M-unknown")
        assert exc.value.details["reason"] == "no_telemetry"

    def test_require_parked_blocks_while_moving(self):
        state.record_tick(_tick(machine_speed=6.0, park_brake=False, gear_state="forward"))
        with pytest.raises(MachineNotParkedError):
            require_parked("M-1")

    def test_require_parked_allows_parked_machine(self):
        state.record_tick(_tick())
        require_parked("M-1")


# ---------------------------------------------------------------------------
# Manual retrieval
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_split_sections_uses_level_two_headings(self):
        manual = ManualEntry(manual_id="m", title="M", machine_type=None, path="x")
        sections = split_sections(manual, "# M\n\nintro\n\n## One\nalpha\n\n## Two\nbeta gamma\n")
        assert [s.section_title for s in sections] == ["One", "Two"]
        assert sections[1].text == "beta gamma"

    def test_finds_the_relevant_section(self):
        hits = get_manual_index().search(
            "how do I use the hydraulic lockout lever", top_k=3, min_score=0.05
        )
        assert hits
        assert hits[0].section.section_title == "Hydraulic lockout lever"

    def test_machine_type_filter_keeps_general_manuals(self):
        hits = get_manual_index().search(
            "tyre pressure", top_k=5, min_score=0.01, machine_type="excavator"
        )
        assert all(h.section.machine_type in (None, "excavator") for h in hits)

    def test_unrelated_query_returns_nothing(self):
        assert get_manual_index().search("zzqx wibble", top_k=3, min_score=0.05) == []

    def test_empty_index_is_rejected(self):
        with pytest.raises(ValueError):
            ManualIndex([])


# ---------------------------------------------------------------------------
# Live stream and progress
# ---------------------------------------------------------------------------


def _machine():
    return SimpleNamespace(
        machine_id="M-1", machine_type="excavator", machine_age=2, rated_max_rpm=2000.0,
        tilt_limit_degrees=30.0, last_service_engine_hours=100.0, engine_hours=420.0, bucket_capacity=1.2,
    )


def _shift():
    return SimpleNamespace(
        shift_id="SH-1", operator_id="OP-1", weather_actual="clear", weather_forecast="clear"
    )


def _task():
    return SimpleNamespace(
        task_id="T-1", task_type="excavation", operator_skill_at_assignment="intermediate",
        material_type="clay", quantity_unit="m3",
    )


class TestLiveStream:
    def test_tick_uses_shift_operator_and_shift_ids(self):
        tick = LiveStream("M-1", seed=42)._build_tick(T0, _machine(), _shift(), None)
        assert tick["operator_id"] == "OP-1"
        assert tick["shift_id"] == "SH-1"
        assert tick["task_id"] is None

    def test_working_task_completes_cycles_at_model_pace(self):
        stream = LiveStream("M-1", seed=42)
        machine, shift, task = _machine(), _shift(), _task()
        cycle_s = _cycle_seconds_for(machine, shift, task)

        ticks = [
            stream._build_tick(T0 + timedelta(seconds=i), machine, shift, task)
            for i in range(cycle_s * 3)
        ]
        cycles = [t for t in ticks if t["load_cycles"] == 1]
        assert len(cycles) == 3
        assert all(t["cycle_payload_pct"] is not None for t in cycles)
        assert all(t["cycle_payload_pct"] < 105.0 for t in cycles)

    def test_no_cycles_without_a_task(self):
        stream = LiveStream("M-1", seed=42)
        ticks = [
            stream._build_tick(T0 + timedelta(seconds=i), _machine(), _shift(), None)
            for i in range(200)
        ]
        assert all(t["load_cycles"] == 0 for t in ticks)


class TestCycleQuantity:
    def test_loads_count_one_per_cycle(self):
        assert cycle_quantity(QuantityUnit.LOADS.value, 90.0, 1.5) == 1.0

    def test_m3_uses_fill_and_bucket_capacity(self):
        assert cycle_quantity(QuantityUnit.M3.value, 50.0, 2.0) == pytest.approx(1.0)
