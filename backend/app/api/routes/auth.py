"""Authentication routes.

POST /api/auth/login          Online login through the cloud (username + password).
POST /api/auth/offline-login  Edge login with the shift's offline credential (username + PIN).

While the cloud link is cut, operators cannot reach the cloud and must use
their PIN. Admins work on the cloud side, so their login still works.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.audit.service import record_audit
from app.core.connectivity import is_cloud_reachable
from app.core.errors import CloudUnavailableError, UnauthorizedError
from app.core.security import create_access_token, verify_password
from app.db.models.cloud.user import User
from app.db.session import get_db
from app.edge.auth.offline_login import verify_offline_login
from app.edge.outbox import queue_audit
from app.schemas.operator import LoginRequest, LoginResponse, OfflineLoginRequest
from app.shared.enums import AuditAction, UserRole

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    result = await session.execute(
        select(User).where(User.username == body.username)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise UnauthorizedError("Invalid username or password.")
    if user.role == UserRole.OPERATOR.value and not is_cloud_reachable():
        raise CloudUnavailableError(
            "The cloud connection is down. Sign in with your PIN instead.",
            details={"offline_login": True},
        )

    record_audit(session, user.user_id, AuditAction.LOGIN, "user", user.user_id)
    await session.commit()

    token = create_access_token(
        user_id=user.user_id, role=user.role, operator_id=user.linked_operator_id
    )

    return LoginResponse(
        access_token=token,
        role=user.role,
        user_id=user.user_id,
        operator_id=user.linked_operator_id,
    )


@router.post("/offline-login", response_model=LoginResponse)
async def offline_login(
    body: OfflineLoginRequest,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    identity = await verify_offline_login(session, body.username, body.pin)
    queue_audit(session, identity.user_id, AuditAction.OFFLINE_LOGIN, "user", identity.user_id)
    await session.commit()

    token = create_access_token(
        user_id=identity.user_id, role=UserRole.OPERATOR.value, operator_id=identity.operator_id
    )
    return LoginResponse(
        access_token=token,
        role=UserRole.OPERATOR.value,
        user_id=identity.user_id,
        operator_id=identity.operator_id,
        offline=True,
    )
