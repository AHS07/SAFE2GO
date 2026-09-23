"""Cloud schema ORM models — imported here so Alembic metadata discovers them."""
from app.db.models.cloud.analytics import (
    BehaviorBaseline,
    EtaAggregate,
    InjectedAnomaly,
    ShiftSummary,
    SyncConflict,
)
from app.db.models.cloud.edge_records import CloudBehaviorEvent, CloudIncident
from app.db.models.cloud.machine import Machine
from app.db.models.cloud.operator import Operator, OperatorQualification
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.sync import CloudInbox, CloudOutbox
from app.db.models.cloud.task import Task
from app.db.models.cloud.training import AnomalyTrainingMap, QuizResult, TrainingModule
from app.db.models.cloud.user import AuditLog, OfflineCredential, User

__all__ = [
    "Machine",
    "Operator",
    "OperatorQualification",
    "Shift",
    "Task",
    "BehaviorBaseline",
    "EtaAggregate",
    "InjectedAnomaly",
    "ShiftSummary",
    "SyncConflict",
    "AnomalyTrainingMap",
    "CloudOutbox",
    "CloudInbox",
    "CloudIncident",
    "CloudBehaviorEvent",
    "QuizResult",
    "TrainingModule",
    "AuditLog",
    "OfflineCredential",
    "User",
]
