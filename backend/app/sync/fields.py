"""Fields carried by each sync message type.

Both tiers build payloads from these lists and handlers apply them, so a
column added to one side is added here once.
"""
from __future__ import annotations

MACHINE_FIELDS = (
    "machine_id",
    "machine_model",
    "machine_type",
    "machine_age",
    "machine_status",
    "service_interval_hours",
    "last_service_engine_hours",
    "engine_hours",
    "bucket_capacity",
    "tilt_limit_degrees",
    "rated_max_rpm",
)

SHIFT_FIELDS = (
    "shift_id",
    "machine_id",
    "operator_id",
    "date",
    "scheduled_start",
    "scheduled_end",
    "weather_forecast",
    "weather_actual",
)

# Set by the cloud at assignment or reassignment.
TASK_ASSIGNMENT_FIELDS = (
    "shift_id",
    "task_type",
    "operator_skill_at_assignment",
    "target_quantity",
    "quantity_unit",
    "material_type",
    "scheduled_start",
    "scheduled_end",
    "reassigned_at",
    "raw_predicted_time",
    "planning_eta",
    "model_version",
    "aggregates_version",
    "is_fallback",
)

# Owned by the edge once the task starts.
TASK_EDGE_FIELDS = (
    "status",
    "completed_quantity",
    "actual_start",
    "actual_end",
    "paused_minutes",
    "blocked_minutes",
    "revised_predicted_time",
    "revised_at",
)

INCIDENT_FIELDS = (
    "incident_id",
    "shift_id",
    "machine_id",
    "operator_id",
    "task_id",
    "event_start",
    "event_end",
    "incident_type",
    "peak_severity",
    "escalation_reason",
    "status",
    "source",
    "description",
    "acknowledged_at",
    "acknowledged_by",
)

BEHAVIOR_EVENT_FIELDS = (
    "event_id",
    "machine_id",
    "operator_id",
    "task_id",
    "shift_id",
    "event_type",
    "start",
    "end",
    "magnitude",
    "baseline_value",
)

QUIZ_RESULT_FIELDS = ("result_id", "operator_id", "module_id", "score", "completed_at")

BASELINE_FIELDS = (
    "baseline_id",
    "scope",
    "scope_id",
    "machine_type",
    "metric",
    "median",
    "mad",
    "sample_count",
    "version",
)

AGGREGATE_FIELDS = (
    "aggregate_id",
    "scope",
    "scope_id",
    "task_type",
    "avg_minutes_per_unit",
    "avg_idle_ratio",
    "avg_cycle_rate",
    "version",
)

SHIFT_SUMMARY_FIELDS = (
    "shift_id",
    "engine_hours",
    "idle_minutes",
    "idle_ratio",
    "cycle_count",
    "completed_quantity",
    "fuel_used",
    "cycle_rate",
)

DATETIME_FIELDS = frozenset({
    "date",
    "scheduled_start",
    "scheduled_end",
    "reassigned_at",
    "actual_start",
    "actual_end",
    "revised_at",
    "event_start",
    "event_end",
    "acknowledged_at",
    "start",
    "end",
    "completed_at",
    "expires_at",
    "timestamp",
})
