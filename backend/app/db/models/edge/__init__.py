"""Edge schema ORM models — imported here so Alembic metadata discovers them."""
from app.db.models.edge.assignment import (
    EdgeBehaviorBaseline,
    EdgeEtaAggregate,
    EdgeMachine,
    EdgeOfflineCredential,
    EdgeOperator,
    EdgeOperatorQualification,
    EdgeShift,
    EdgeTask,
)
from app.db.models.edge.behavior import BehaviorEvent, Recommendation
from app.db.models.edge.incident import Incident
from app.db.models.edge.sync import EdgeInbox, EdgeOutbox
from app.db.models.edge.telemetry import Telemetry
from app.db.models.edge.training import EdgeAnomalyTrainingMap, EdgeQuizResult, EdgeTrainingModule

__all__ = [
    "Telemetry",
    "Incident",
    "BehaviorEvent",
    "Recommendation",
    "EdgeOutbox",
    "EdgeInbox",
    "EdgeMachine",
    "EdgeOperator",
    "EdgeOperatorQualification",
    "EdgeShift",
    "EdgeTask",
    "EdgeBehaviorBaseline",
    "EdgeEtaAggregate",
    "EdgeOfflineCredential",
    "EdgeAnomalyTrainingMap",
    "EdgeQuizResult",
    "EdgeTrainingModule",
]
