"""Operator sign-in accounts.

Every operator in the roster can be assigned work, but only an operator
with a user account can sign in to see it. The admin creates the account
here: a password for online login and a PIN for offline login.

Offline credentials are normally issued when a shift is created. An
operator who gets an account later already has shifts, so credentials are
issued for those shifts that are still valid, and the roster is resent so
the edge knows the new username.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.audit.service import record_audit
from app.cloud.credentials.issuer import issue_for_shift
from app.cloud.publish import queue_credential, queue_operator_roster
from app.config.settings import get_settings
from app.core.clock import sim_now
from app.core.errors import NotFoundError, ValidationError
from app.core.security import hash_password
from app.db.models.cloud.operator import Operator
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.user import User
from app.shared.enums import AuditAction, UserRole

log = logging.getLogger("safe2go.accounts")

# bcrypt uses only the first 72 bytes of a password.
_PASSWORD_MAX_BYTES = 72


@dataclass
class AccountResult:
    user: User
    credentials_issued: int


def _field_error(message: str, field: str, problem: str) -> ValidationError:
    return ValidationError(message, details={"fields": [{"field": field, "problem": problem}]})


async def operator_accounts(session: AsyncSession) -> dict[str, User]:
    """Operator id to user account, for operators that have one."""
    users = (
        await session.execute(
            select(User)
            .where(User.role == UserRole.OPERATOR.value)
            .where(User.linked_operator_id.is_not(None))
        )
    ).scalars().all()
    return {u.linked_operator_id: u for u in users}


async def create_operator_account(
    session: AsyncSession,
    operator_id: str,
    *,
    username: str,
    password: str,
    pin: str,
    actor_id: str | None,
) -> AccountResult:
    """Create the operator's user account and issue credentials for their current and upcoming shifts."""
    if await session.get(Operator, operator_id) is None:
        raise NotFoundError(f"Operator {operator_id} not found.", details={"operator_id": operator_id})
    if operator_id in await operator_accounts(session):
        raise ValidationError("This operator already has a sign-in account.", details={"operator_id": operator_id})
    if len(password.encode()) > _PASSWORD_MAX_BYTES:
        raise _field_error("The password is too long.", "password", f"at most {_PASSWORD_MAX_BYTES} bytes")
    taken = (await session.execute(select(User.user_id).where(User.username == username))).scalar_one_or_none()
    if taken is not None:
        raise _field_error("That username is already in use.", "username", "already in use")

    user = User(
        user_id=str(uuid.uuid4()),
        role=UserRole.OPERATOR.value,
        linked_operator_id=operator_id,
        username=username,
        password_hash=hash_password(password),
        pin_hash=hash_password(pin),
    )
    session.add(user)
    await session.flush()

    # The edge finds the operator by username for offline login.
    await queue_operator_roster(session)

    # Shifts whose credential would still be valid now (same expiry as the issuer).
    grace = timedelta(minutes=get_settings().offline_credential_grace_minutes)
    shifts = (
        await session.execute(
            select(Shift).where(Shift.operator_id == operator_id).where(Shift.scheduled_end > sim_now() - grace)
        )
    ).scalars().all()
    issued = 0
    for shift in shifts:
        credential = await issue_for_shift(session, shift)
        if credential is not None:
            queue_credential(session, credential)
            issued += 1

    record_audit(session, actor_id, AuditAction.ACCOUNT_CREATE, "user", user.user_id)
    log.info(
        "Operator account created",
        extra={"operator_id": operator_id, "user_id": user.user_id, "credentials_issued": issued},
    )
    return AccountResult(user, issued)
