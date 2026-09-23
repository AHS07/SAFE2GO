"""FastAPI dependency functions for authentication and roles.

Failures raise domain errors so every response uses the standard error
envelope (rules.md section 5).
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.shared.enums import UserRole

_bearer = HTTPBearer(auto_error=False)


def _get_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Sign in to continue.")
    return credentials.credentials


def get_current_user(token: str = Depends(_get_token)) -> dict:
    """Decode the session token and return its payload."""
    return decode_access_token(token)


def require_operator(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != UserRole.OPERATOR.value:
        raise ForbiddenError("Operator access required.")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != UserRole.ADMIN.value:
        raise ForbiddenError("Admin access required.")
    return user


def require_any_role(user: dict = Depends(get_current_user)) -> dict:
    return user
