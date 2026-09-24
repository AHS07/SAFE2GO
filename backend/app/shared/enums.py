"""Shared enumerations used across cloud, edge, and sync modules."""
from __future__ import annotations

from enum import StrEnum


class MachineType(StrEnum):
    EXCAVATOR = "excavator"
    WHEEL_LOADER = "wheel_loader"
    BACKHOE_LOADER = "backhoe_loader"


class MachineStatus(StrEnum):
    AVAILABLE = "available"
    MAINTENANCE = "maintenance"


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"


class TaskType(StrEnum):
    EXCAVATION = "excavation"
    MATERIAL_LOADING = "material_loading"
    TRENCHING = "trenching"


class MaterialType(StrEnum):
    CLAY = "clay"
    SANDY_SOIL = "sandy_soil"
    GRAVEL = "gravel"
    ROCK = "rock"
    MIXED_FILL = "mixed_fill"
    TOPSOIL = "topsoil"


class QuantityUnit(StrEnum):
    M3 = "m3"
    LOADS = "loads"


class TaskStatus(StrEnum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class IncidentType(StrEnum):
    SEATBELT = "seatbelt"
    OPERATOR_NOT_SEATED = "operator_not_seated"
    PROXIMITY = "proximity"
    OVERLOADING = "overloading"
    TILT = "tilt"
    WORKING_CONDITION = "working_condition"
    MANUAL_REPORT = "manual_report"


class Severity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class EscalationReason(StrEnum):
    GRACE_PERIOD = "grace_period"
    THRESHOLD = "threshold"
    REPEAT = "repeat"


class BehaviorEventType(StrEnum):
    EXCESSIVE_IDLING = "excessive_idling"
    REPEATED_OVERLOADING = "repeated_overloading"
    HIGH_RPM_TRAVEL = "high_rpm_travel"
    REPEATED_SEATBELT_VIOLATION = "repeated_seatbelt_violation"
    HIGH_IDLE_RATIO = "high_idle_ratio"


class WeatherCategory(StrEnum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    FOG = "fog"
    STORM = "storm"


class UserRole(StrEnum):
    OPERATOR = "operator"
    ADMIN = "admin"


class SafetyRuleState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    ACTIVE = "active"
    CLEARING = "clearing"
    CLOSED = "closed"


class AnomalyType(StrEnum):
    """Used in the injected_anomalies table (ground truth only)."""

    EXCESSIVE_IDLING = "excessive_idling"
    REPEATED_OVERLOADING = "repeated_overloading"
    HIGH_RPM_TRAVEL = "high_rpm_travel"
    REPEATED_SEATBELT_VIOLATION = "repeated_seatbelt_violation"
    HIGH_IDLE_RATIO = "high_idle_ratio"
    LEGIT_WAIT = "legit_wait"


class SyncMessageType(StrEnum):
    # Cloud to edge
    SHIFT_RECORD = "shift_record"
    TASK_ASSIGNED = "task_assigned"
    TASK_CANCELLED = "task_cancelled"
    TASK_REASSIGNED = "task_reassigned"
    ETA_MODEL_REF = "eta_model_ref"
    BEHAVIOR_BASELINE = "behavior_baseline"
    TRAINING_CONTENT = "training_content"
    MACHINE_MASTER = "machine_master"
    OPERATOR_ROSTER = "operator_roster"
    OFFLINE_CREDENTIAL = "offline_credential"

    # Edge to cloud
    SHIFT_SUMMARY = "shift_summary"
    INCIDENT = "incident"
    BEHAVIOR_EVENT = "behavior_event"
    TASK_STATUS = "task_status"
    QUIZ_RESULT = "quiz_result"
    AUDIT_RECORD = "audit_record"


class ConflictType(StrEnum):
    REASSIGN_AFTER_START = "reassign_after_start"
    CANCEL_AFTER_START = "cancel_after_start"


class Tier(StrEnum):
    CLOUD = "cloud"
    EDGE = "edge"


class AuditAction(StrEnum):
    LOGIN = "login"
    SHIFT_CREATE = "shift_create"
    TASK_CREATE = "task_create"
    TASK_CANCEL = "task_cancel"
    TASK_REASSIGN = "task_reassign"
    CONNECTIVITY_TOGGLE = "connectivity_toggle"
    OFFLINE_LOGIN = "offline_login"
    TASK_STATUS_CHANGE = "task_status_change"
    INCIDENT_ACKNOWLEDGE = "incident_acknowledge"
    INCIDENT_REPORT = "incident_report"
    CONFLICT_RESOLVE = "conflict_resolve"
    SYNC_RETRY = "sync_retry"
