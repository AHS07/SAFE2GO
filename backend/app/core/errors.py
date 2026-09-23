"""Domain error classes.

Every business error is a subclass of Safe2GoError with a stable string code.
HTTP mapping lives in error_handlers.py, not here.
"""
from __future__ import annotations


class Safe2GoError(Exception):
    """Base class for all domain errors."""

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- 400 Validation ---


class ValidationError(Safe2GoError):
    code = "VALIDATION_ERROR"


# --- 401 / 403 Auth ---


class UnauthorizedError(Safe2GoError):
    code = "UNAUTHORIZED"


class ForbiddenError(Safe2GoError):
    code = "FORBIDDEN"


# --- 404 Not Found ---


class NotFoundError(Safe2GoError):
    code = "NOT_FOUND"


# --- 409 Business conflicts ---


class OperatorNotQualifiedError(Safe2GoError):
    code = "OPERATOR_NOT_QUALIFIED"


class TaskMachineIncompatibleError(Safe2GoError):
    code = "TASK_MACHINE_INCOMPATIBLE"


class MachineInMaintenanceError(Safe2GoError):
    code = "MACHINE_IN_MAINTENANCE"


class ShiftOverlapError(Safe2GoError):
    code = "SHIFT_OVERLAP"


class TaskOverlapError(Safe2GoError):
    code = "TASK_OVERLAP"


class TaskAlreadyStartedError(Safe2GoError):
    code = "TASK_ALREADY_STARTED"


class InvalidStatusTransitionError(Safe2GoError):
    code = "INVALID_STATUS_TRANSITION"


class AckRequiresStationaryError(Safe2GoError):
    code = "ACK_REQUIRES_STATIONARY"


class MachineNotParkedError(Safe2GoError):
    code = "MACHINE_NOT_PARKED"


# --- 503 Availability ---


class CloudUnavailableError(Safe2GoError):
    code = "CLOUD_UNAVAILABLE"


class LLMUnavailableError(Safe2GoError):
    code = "LLM_UNAVAILABLE"


# --- 500 Internal ---


class InternalError(Safe2GoError):
    code = "INTERNAL_ERROR"
