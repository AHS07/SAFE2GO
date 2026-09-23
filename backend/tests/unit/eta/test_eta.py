"""Phase 5 tests: task time estimation.

Exit criteria from phases.md:
  - Training is reproducible with the fixed seed.
  - raw_predicted_time never changes after assignment.
  - Model load failure produces a fallback estimate, not an error.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import joblib
import numpy as np
import pytest

from app.cloud.eta.service import apply_assignment_estimate
from app.config.thresholds import ETAModelConfig, get_thresholds
from app.core.errors import ValidationError
from app.edge.eta import revision as revision_module
from app.edge.eta.revision import (
    TRIGGER_PACE,
    TRIGGER_WEATHER,
    RevisionState,
    elapsed_working_minutes,
    revise_if_triggered,
    revision_trigger,
)
from app.shared.enums import (
    MachineType,
    MaterialType,
    QuantityUnit,
    SkillLevel,
    TaskStatus,
    TaskType,
    WeatherCategory,
)
from app.shared.eta_aggregates import (
    DEFAULT_MINUTES_PER_UNIT,
    MIN_SAMPLES,
    SCOPE_DEFAULT,
    SCOPE_FLEET,
    SCOPE_MACHINE,
    SCOPE_OPERATOR,
    SCOPE_SKILL,
    AggregateStats,
    AggregateTable,
    HistoryFeatures,
    ShiftHistory,
    TaskHistory,
    aggregates_version,
)
from app.shared.eta_model import (
    FALLBACK_MODEL_VERSION,
    EtaEstimate,
    EtaInput,
    EtaPredictor,
    buffer_minutes,
    displayed_eta,
    explain,
    load_predictor,
)
from ml.features import Dataset, TaskSample, evaluation_matrix, time_split, training_matrix
from ml.train_eta import train_model

T0 = datetime(2025, 1, 1, 7, tzinfo=UTC)
SMALL_MODEL = ETAModelConfig(n_estimators=20, max_depth=6, random_state=42)


# ---------------------------------------------------------------------------
# Synthetic history helpers (no database)
# ---------------------------------------------------------------------------


def _task(
    idx: int,
    operator: str = "op-1",
    machine: str = "m-1",
    skill: str = SkillLevel.INTERMEDIATE.value,
    task_type: str = TaskType.EXCAVATION.value,
    quantity: float = 100.0,
    minutes: float = 60.0,
    shift: str | None = None,
) -> TaskHistory:
    return TaskHistory(
        task_id=f"t-{idx}",
        shift_id=shift or f"s-{idx}",
        operator_id=operator,
        machine_id=machine,
        machine_type=MachineType.EXCAVATOR.value,
        skill_level=skill,
        task_type=task_type,
        target_quantity=quantity,
        actual_minutes=minutes,
    )


def _shift(idx: int, operator: str = "op-1", machine: str = "m-1", idle: float = 0.2) -> ShiftHistory:
    return ShiftHistory(
        shift_id=f"s-{idx}",
        operator_id=operator,
        machine_id=machine,
        machine_type=MachineType.EXCAVATOR.value,
        skill_level=SkillLevel.INTERMEDIATE.value,
        idle_ratio=idle,
        cycle_rate=40.0,
    )


def _synthetic_dataset(n_shifts: int = 60, seed: int = 7) -> Dataset:
    """Tasks whose duration depends on quantity, skill and weather."""
    rng = np.random.default_rng(seed)
    skills = list(SkillLevel)
    skill_factor = {SkillLevel.BEGINNER: 1.3, SkillLevel.INTERMEDIATE: 1.0, SkillLevel.EXPERT: 0.8}
    weather_factor = {w: 1.0 + 0.1 * i for i, w in enumerate(WeatherCategory)}
    tasks: list[TaskSample] = []
    shifts: list[ShiftHistory] = []
    dates: dict[str, datetime] = {}
    for s in range(n_shifts):
        date = T0 + timedelta(days=s // 3)
        operator = f"op-{s % 5}"
        machine = f"m-{s % 4}"
        skill = skills[s % 5 % 3]
        weather = list(WeatherCategory)[int(rng.integers(0, 5))]
        shift_id = f"s-{s}"
        dates[shift_id] = date
        shifts.append(ShiftHistory(
            shift_id, operator, machine, MachineType.EXCAVATOR.value, skill.value,
            float(rng.uniform(0.1, 0.3)), float(rng.uniform(30, 50)),
        ))
        for k in range(3):
            qty = float(rng.uniform(50, 200))
            minutes = qty * 0.5 * skill_factor[skill] * weather_factor[weather] * float(rng.uniform(0.9, 1.1))
            history = TaskHistory(
                f"t-{s}-{k}", shift_id, operator, machine, MachineType.EXCAVATOR.value,
                skill.value, TaskType.EXCAVATION.value, qty, minutes,
            )
            tasks.append(TaskSample(
                shift_date=date,
                unit=QuantityUnit.M3.value,
                inp=EtaInput(
                    TaskType.EXCAVATION.value, MaterialType.CLAY.value, qty, skill.value,
                    MachineType.EXCAVATOR.value, 3, weather.value,
                ),
                history=history,
            ))
    return Dataset(tasks, shifts, dates)


def _history(machine_pace: float = 0.5) -> HistoryFeatures:
    return HistoryFeatures(0.5, machine_pace, 0.2, 40.0, SCOPE_OPERATOR, SCOPE_MACHINE)


def _input(weather: str = WeatherCategory.CLEAR.value, quantity: float = 100.0) -> EtaInput:
    return EtaInput(
        TaskType.EXCAVATION.value, MaterialType.CLAY.value, quantity,
        SkillLevel.INTERMEDIATE.value, MachineType.EXCAVATOR.value, 3, weather,
    )


# ---------------------------------------------------------------------------
# Aggregates and cold-start fallback
# ---------------------------------------------------------------------------


class TestAggregates:
    def test_operator_scope_used_when_enough_history(self) -> None:
        tasks = [_task(i, minutes=50.0) for i in range(MIN_SAMPLES)]
        stats = AggregateStats(tasks, [_shift(i) for i in range(MIN_SAMPLES)])
        table = AggregateTable(stats.to_rows(), "v")
        h = table.history("op-1", "m-1", MachineType.EXCAVATOR.value, SkillLevel.INTERMEDIATE.value,
                          TaskType.EXCAVATION.value)
        assert h.operator_scope == SCOPE_OPERATOR
        assert h.machine_scope == SCOPE_MACHINE
        assert h.operator_minutes_per_unit == pytest.approx(0.5)

    def test_new_operator_falls_back_to_skill(self) -> None:
        tasks = [_task(i) for i in range(MIN_SAMPLES)]
        table = AggregateTable(AggregateStats(tasks, []).to_rows(), "v")
        h = table.history("op-new", "m-1", MachineType.EXCAVATOR.value,
                          SkillLevel.INTERMEDIATE.value, TaskType.EXCAVATION.value)
        assert h.operator_scope == SCOPE_SKILL

    def test_new_operator_and_skill_fall_back_to_fleet(self) -> None:
        tasks = [_task(i) for i in range(MIN_SAMPLES)]
        table = AggregateTable(AggregateStats(tasks, []).to_rows(), "v")
        h = table.history("op-new", "m-new", MachineType.EXCAVATOR.value,
                          SkillLevel.EXPERT.value, TaskType.EXCAVATION.value)
        assert h.operator_scope == SCOPE_FLEET
        assert h.machine_scope == SCOPE_FLEET

    def test_no_history_uses_defaults(self) -> None:
        table = AggregateTable([], "none")
        h = table.history("op", "m", MachineType.EXCAVATOR.value, SkillLevel.BEGINNER.value,
                          TaskType.TRENCHING.value)
        assert h.operator_scope == SCOPE_DEFAULT
        assert h.machine_minutes_per_unit == DEFAULT_MINUTES_PER_UNIT[TaskType.TRENCHING.value]

    def test_scope_below_min_samples_is_not_trusted(self) -> None:
        tasks = [_task(i) for i in range(MIN_SAMPLES - 1)]
        assert AggregateStats(tasks, []).to_rows() == []

    def test_pace_is_total_minutes_over_total_quantity(self) -> None:
        tasks = [_task(0, quantity=100, minutes=50), _task(1, quantity=300, minutes=250),
                 _task(2, quantity=100, minutes=100)]
        row = AggregateStats(tasks, []).resolver()(SCOPE_OPERATOR, "op-1", TaskType.EXCAVATION.value)
        assert row is not None
        assert row.avg_minutes_per_unit == pytest.approx(400 / 500)

    def test_leave_one_out_excludes_own_row(self) -> None:
        tasks = [_task(i, minutes=50.0) for i in range(MIN_SAMPLES)]
        tasks.append(_task(99, minutes=500.0))
        stats = AggregateStats(tasks, [])
        with_own = stats.resolver()(SCOPE_OPERATOR, "op-1", TaskType.EXCAVATION.value)
        without_own = stats.resolver(exclude_task=tasks[-1])(SCOPE_OPERATOR, "op-1", TaskType.EXCAVATION.value)
        assert with_own is not None and without_own is not None
        assert without_own.avg_minutes_per_unit == pytest.approx(0.5)
        assert with_own.avg_minutes_per_unit > without_own.avg_minutes_per_unit

    def test_leave_one_out_excludes_own_shift_idle_ratio(self) -> None:
        tasks = [_task(i) for i in range(MIN_SAMPLES)]
        shifts = [_shift(0, idle=0.1), _shift(1, idle=0.1), _shift(2, idle=0.9)]
        resolve = AggregateStats(tasks, shifts).resolver(exclude_shift=shifts[2])
        row = resolve(SCOPE_OPERATOR, "op-1", TaskType.EXCAVATION.value)
        assert row is not None
        assert row.avg_idle_ratio == pytest.approx(0.1)

    def test_version_is_deterministic_and_content_based(self) -> None:
        rows = AggregateStats([_task(i) for i in range(MIN_SAMPLES)], []).to_rows()
        assert aggregates_version(rows) == aggregates_version(list(rows))
        other = AggregateStats([_task(i, minutes=99) for i in range(MIN_SAMPLES)], []).to_rows()
        assert aggregates_version(rows) != aggregates_version(other)


# ---------------------------------------------------------------------------
# Training and evaluation data handling
# ---------------------------------------------------------------------------


class TestTraining:
    def test_time_split_is_ordered(self) -> None:
        split = time_split(_synthetic_dataset(), train_days=15)
        assert split.train.tasks and split.test.tasks
        assert max(t.shift_date for t in split.train.tasks) < split.split_date
        assert min(t.shift_date for t in split.test.tasks) >= split.split_date

    def test_training_is_reproducible(self) -> None:
        split = time_split(_synthetic_dataset(), train_days=15)
        a = train_model(split.train, SMALL_MODEL)
        b = train_model(split.train, SMALL_MODEL)
        assert a["model_version"] == b["model_version"]
        x = evaluation_matrix(split.train, split.test).x
        np.testing.assert_array_equal(a["model"].predict(x), b["model"].predict(x))

    def test_model_version_changes_with_data(self) -> None:
        a = train_model(time_split(_synthetic_dataset(seed=1), 15).train, SMALL_MODEL)
        b = train_model(time_split(_synthetic_dataset(seed=2), 15).train, SMALL_MODEL)
        assert a["model_version"] != b["model_version"]

    def test_evaluation_aggregates_ignore_test_rows(self) -> None:
        split = time_split(_synthetic_dataset(), train_days=15)
        before = evaluation_matrix(split.train, split.test).x
        # Inflating test targets must not change test features.
        inflated = Dataset(
            [TaskSample(t.shift_date, t.unit, t.inp,
                        TaskHistory(**{**t.history.__dict__, "actual_minutes": 9999.0}))
             for t in split.test.tasks],
            split.test.shifts, split.test.shift_dates,
        )
        after = evaluation_matrix(split.train, inflated).x
        np.testing.assert_array_equal(before, after)

    def test_training_rows_do_not_see_own_target(self) -> None:
        data = _synthetic_dataset()
        split = time_split(data, train_days=15)
        base = training_matrix(split.train).x
        first = split.train.tasks[0]
        changed = Dataset(
            [TaskSample(first.shift_date, first.unit, first.inp,
                        TaskHistory(**{**first.history.__dict__, "actual_minutes": 9999.0}))]
            + split.train.tasks[1:],
            split.train.shifts, split.train.shift_dates,
        )
        np.testing.assert_allclose(training_matrix(changed).x[0], base[0], rtol=1e-9)

    def test_model_beats_historical_average_on_learnable_data(self) -> None:
        split = time_split(_synthetic_dataset(n_shifts=150), train_days=40)
        artifact = train_model(split.train, get_thresholds().eta.model)
        matrix = evaluation_matrix(split.train, split.test)
        model_err = np.mean(np.abs(artifact["model"].predict(matrix.x) - matrix.y))
        hist = np.array([h.historical_average_minutes(s.inp.target_quantity)
                         for s, h in zip(matrix.samples, matrix.histories, strict=True)])
        assert model_err < np.mean(np.abs(hist - matrix.y))


# ---------------------------------------------------------------------------
# Predictor and fallback
# ---------------------------------------------------------------------------


class TestPredictor:
    def test_missing_artifact_gives_fallback(self, tmp_path) -> None:
        predictor = load_predictor(tmp_path / "missing.joblib")
        assert not predictor.available
        est = predictor.predict(_input(), _history(machine_pace=0.6), "m3")
        assert est.is_fallback
        assert est.model_version == FALLBACK_MODEL_VERSION
        assert est.raw_minutes == pytest.approx(60.0)

    def test_corrupt_artifact_gives_fallback(self, tmp_path) -> None:
        path = tmp_path / "bad.joblib"
        path.write_bytes(b"not a joblib file")
        assert not load_predictor(path).available

    def test_artifact_with_wrong_features_gives_fallback(self, tmp_path) -> None:
        path = tmp_path / "old.joblib"
        joblib.dump({"model": None, "feature_names": ["x"], "model_version": "v", "reference": {}}, path)
        assert not load_predictor(path).available

    def test_prediction_error_gives_fallback(self) -> None:
        class Broken:
            def predict(self, x):
                raise RuntimeError("boom")

        predictor = EtaPredictor({"model": Broken(), "model_version": "rf-x", "reference": {}})
        est = predictor.predict(_input(), _history(), "m3")
        assert est.is_fallback

    def test_saved_artifact_round_trip(self, tmp_path) -> None:
        split = time_split(_synthetic_dataset(), train_days=15)
        artifact = train_model(split.train, SMALL_MODEL)
        path = tmp_path / "eta.joblib"
        joblib.dump(artifact, path)
        predictor = load_predictor(path)
        assert predictor.available
        est = predictor.predict(_input(), _history(), "m3")
        assert not est.is_fallback
        assert est.model_version == artifact["model_version"]
        assert est.raw_minutes >= 1.0

    def test_displayed_eta_uses_revision_plus_buffer(self) -> None:
        assert displayed_eta(75.0, None) == 75.0
        assert displayed_eta(75.0, 90.0) == 90.0 + buffer_minutes()
        assert displayed_eta(None, None) is None


class TestExplanation:
    def _reference(self) -> dict:
        split = time_split(_synthetic_dataset(n_shifts=90), train_days=20)
        return train_model(split.train, SMALL_MODEL)["reference"]

    def test_rare_weather_and_large_quantity_are_flagged(self) -> None:
        reference = self._reference()
        reference["categorical"]["weather"] = {"clear": 0.6, "storm": 0.05}
        found = explain(_input(WeatherCategory.STORM.value, quantity=10_000), _history(), reference, "m3")
        names = [f.feature for f in found]
        assert "weather" in names
        assert "target_quantity" in names
        assert len(found) <= 3

    def test_typical_task_has_no_unusual_features(self) -> None:
        reference = self._reference()
        reference["categorical"] = {}
        median_qty = reference["numeric"][TaskType.EXCAVATION.value]["target_quantity"][50]
        median_age = reference["numeric"][TaskType.EXCAVATION.value]["machine_age"][50]
        history = HistoryFeatures(
            reference["numeric"][TaskType.EXCAVATION.value]["operator_minutes_per_unit"][50],
            0.5,
            reference["numeric"][TaskType.EXCAVATION.value]["operator_idle_ratio"][50],
            40.0, SCOPE_OPERATOR, SCOPE_MACHINE,
        )
        inp = EtaInput(TaskType.EXCAVATION.value, MaterialType.CLAY.value, median_qty,
                       SkillLevel.INTERMEDIATE.value, MachineType.EXCAVATOR.value,
                       int(median_age), WeatherCategory.CLEAR.value)
        assert explain(inp, history, reference, "m3") == []


# ---------------------------------------------------------------------------
# raw_predicted_time never changes after assignment
# ---------------------------------------------------------------------------


def _task_row(**overrides) -> SimpleNamespace:
    values = dict(
        task_id="task-1",
        shift_id="shift-1",
        task_type=TaskType.EXCAVATION.value,
        material_type=MaterialType.CLAY.value,
        target_quantity=100.0,
        quantity_unit=QuantityUnit.M3.value,
        operator_skill_at_assignment=SkillLevel.INTERMEDIATE.value,
        status=TaskStatus.IN_PROGRESS.value,
        completed_quantity=0.0,
        scheduled_start=T0,
        scheduled_end=None,
        actual_start=T0,
        paused_minutes=0.0,
        blocked_minutes=0.0,
        raw_predicted_time=None,
        planning_eta=None,
        revised_predicted_time=None,
        revised_at=None,
        model_version=None,
        aggregates_version=None,
        is_fallback=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class TestRawPredictionLocked:
    def test_assignment_sets_all_fields(self) -> None:
        task = _task_row()
        apply_assignment_estimate(task, EtaEstimate(60.0, 55.0, False, "rf-1"), "agg-1")
        assert task.raw_predicted_time == 60.0
        assert task.planning_eta == 60.0 + buffer_minutes()
        assert task.scheduled_end == T0 + timedelta(minutes=task.planning_eta)
        assert (task.model_version, task.aggregates_version) == ("rf-1", "agg-1")

    def test_second_assignment_is_rejected(self) -> None:
        task = _task_row()
        apply_assignment_estimate(task, EtaEstimate(60.0, 55.0, False, "rf-1"), "agg-1")
        with pytest.raises(ValidationError):
            apply_assignment_estimate(task, EtaEstimate(99.0, 55.0, False, "rf-2"), "agg-2")
        assert task.raw_predicted_time == 60.0
        assert task.model_version == "rf-1"

    async def test_edge_revision_never_touches_raw(self, monkeypatch) -> None:
        task = _task_row(raw_predicted_time=60.0, planning_eta=75.0, completed_quantity=50.0)
        shift = SimpleNamespace(
            operator_id="op-1", machine_id="m-1",
            weather_forecast=WeatherCategory.CLEAR.value,
            weather_actual=WeatherCategory.STORM.value,
        )
        machine = SimpleNamespace(machine_type=MachineType.EXCAVATOR.value, machine_age=3)

        class FakeSession:
            async def execute(self, _stmt):
                return SimpleNamespace(one_or_none=lambda: (shift, machine))

            async def flush(self) -> None:
                return None

        async def fake_table(_session, _source):
            return AggregateTable([], "none")

        monkeypatch.setattr(revision_module, "load_aggregate_table", fake_table)
        now = T0 + timedelta(minutes=40)
        trigger = await revise_if_triggered(FakeSession(), task, now=now, predictor=EtaPredictor(None))
        assert trigger == TRIGGER_WEATHER
        assert task.raw_predicted_time == 60.0
        assert task.revised_at == now
        # elapsed 40 min + remaining 50 m3 at the default excavation pace
        assert task.revised_predicted_time == pytest.approx(
            40.0 + 50.0 * DEFAULT_MINUTES_PER_UNIT[TaskType.EXCAVATION.value]
        )


# ---------------------------------------------------------------------------
# Edge revision triggers
# ---------------------------------------------------------------------------


def _state(**overrides) -> RevisionState:
    values = dict(
        status=TaskStatus.IN_PROGRESS.value,
        target_quantity=100.0,
        completed_quantity=50.0,
        current_estimate_minutes=60.0,
        elapsed_working_minutes=30.0,   # exactly on pace
        weather_used=WeatherCategory.CLEAR.value,
        weather_observed=WeatherCategory.CLEAR.value,
        last_revised_at=None,
        now=T0 + timedelta(hours=1),
    )
    values.update(overrides)
    return RevisionState(**values)


class TestRevisionTriggers:
    cfg = get_thresholds().eta

    def test_on_pace_same_weather_no_trigger(self) -> None:
        assert revision_trigger(_state(), self.cfg) is None

    def test_weather_change_triggers(self) -> None:
        assert revision_trigger(_state(weather_observed="rain"), self.cfg) == TRIGGER_WEATHER

    def test_slow_pace_triggers(self) -> None:
        assert revision_trigger(_state(elapsed_working_minutes=40.0), self.cfg) == TRIGGER_PACE

    def test_fast_pace_triggers(self) -> None:
        assert revision_trigger(_state(elapsed_working_minutes=20.0), self.cfg) == TRIGGER_PACE

    def test_small_deviation_does_not_trigger(self) -> None:
        # 50 done in 35 min vs expected 30 min: rate off by about 14%
        assert revision_trigger(_state(elapsed_working_minutes=35.0), self.cfg) is None

    def test_pace_ignored_before_25_percent(self) -> None:
        state = _state(completed_quantity=20.0, elapsed_working_minutes=40.0)
        assert revision_trigger(state, self.cfg) is None

    def test_rate_limit_blocks_second_revision(self) -> None:
        now = T0 + timedelta(hours=1)
        recent = now - timedelta(seconds=self.cfg.revision_rate_limit_seconds - 1)
        old = now - timedelta(seconds=self.cfg.revision_rate_limit_seconds)
        assert revision_trigger(_state(weather_observed="rain", last_revised_at=recent, now=now), self.cfg) is None
        assert revision_trigger(_state(weather_observed="rain", last_revised_at=old, now=now), self.cfg) == TRIGGER_WEATHER

    def test_only_in_progress_tasks_revise(self) -> None:
        for status in (TaskStatus.PAUSED, TaskStatus.BLOCKED, TaskStatus.DONE):
            assert revision_trigger(_state(status=status.value, weather_observed="rain"), self.cfg) is None

    def test_elapsed_excludes_paused_and_blocked(self) -> None:
        task = _task_row(paused_minutes=10.0, blocked_minutes=5.0)
        assert elapsed_working_minutes(task, T0 + timedelta(minutes=60)) == pytest.approx(45.0)
