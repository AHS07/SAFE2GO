"""FastAPI exception handlers.

Maps domain errors to HTTP responses using the standard error envelope:
  {"error": {"code": "...", "message": "...", "details": {...}}}
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    AckRequiresStationaryError,
    CloudUnavailableError,
    ForbiddenError,
    InternalError,
    InvalidStatusTransitionError,
    LLMUnavailableError,
    MachineInMaintenanceError,
    MachineNotParkedError,
    NotFoundError,
    OperatorNotQualifiedError,
    Safe2GoError,
    ShiftOverlapError,
    TaskAlreadyStartedError,
    TaskMachineIncompatibleError,
    TaskOverlapError,
    UnauthorizedError,
    ValidationError,
)
from app.core.request_context import REQUEST_ID_HEADER, current_request_id

log = logging.getLogger("safe2go.errors")

_STATUS_MAP: dict[str, int] = {
    ValidationError.code: 400,
    UnauthorizedError.code: 401,
    ForbiddenError.code: 403,
    NotFoundError.code: 404,
    OperatorNotQualifiedError.code: 409,
    TaskMachineIncompatibleError.code: 409,
    MachineInMaintenanceError.code: 409,
    ShiftOverlapError.code: 409,
    TaskOverlapError.code: 409,
    TaskAlreadyStartedError.code: 409,
    InvalidStatusTransitionError.code: 409,
    AckRequiresStationaryError.code: 409,
    MachineNotParkedError.code: 409,
    CloudUnavailableError.code: 503,
    LLMUnavailableError.code: 503,
    InternalError.code: 500,
}


# Framework errors (unknown route, wrong method) mapped to the domain codes.
_HTTP_CODES: dict[int, str] = {
    400: ValidationError.code,
    401: UnauthorizedError.code,
    403: ForbiddenError.code,
    404: NotFoundError.code,
    405: NotFoundError.code,
}


def _error_response(code: str, message: str, details: dict, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all domain and fallback error handlers to the FastAPI app."""

    @app.exception_handler(Safe2GoError)
    async def handle_domain_error(request: Request, exc: Safe2GoError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return _error_response(exc.code, exc.message, exc.details, status)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(part) for part in err["loc"][1:]), "problem": err["msg"]}
            for err in exc.errors()
        ]
        return _error_response(
            ValidationError.code, "Some of the submitted values are not valid.", {"fields": fields}, 400
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, InternalError.code)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(code, message, {}, exc.status_code)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or current_request_id() or uuid.uuid4().hex
        # Full detail goes to the log; only a generic message is returned.
        log.error("Unhandled exception", extra={"request_id": request_id}, exc_info=exc)
        response = _error_response(
            "INTERNAL_ERROR",
            "An unexpected error occurred.",
            {"request_id": request_id},
            500,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
