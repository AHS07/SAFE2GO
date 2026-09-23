"""Offline credential issuer.

A credential is issued when a shift is created and queued for the edge.
It is valid for that shift's operator on that shift's machine until the
scheduled end plus a grace period (sim time). It carries the PIN verifier,
never the password hash. Operators without a PIN get no credential and can
only sign in online.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.security import issue_offline_credential
from app.db.models.cloud.shift import Shift
from app.db.models.cloud.user import OfflineCredential, User

log = logging.getLogger("safe2go.credentials")


async def issue_for_shift(session: AsyncSession, shift: Shift) -> OfflineCredential | None:
    """Create (or replace) the offline credential for a shift."""
    pin_hash = (
        await session.execute(
            select(User.pin_hash)
            .where(User.linked_operator_id == shift.operator_id)
            .where(User.pin_hash.is_not(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if pin_hash is None:
        log.info("No PIN set, no offline credential issued", extra={"shift_id": shift.shift_id})
        return None

    expires_at = shift.scheduled_end + timedelta(minutes=get_settings().offline_credential_grace_minutes)
    await session.execute(delete(OfflineCredential).where(OfflineCredential.shift_id == shift.shift_id))
    credential = OfflineCredential(
        credential_id=str(uuid.uuid4()),
        shift_id=shift.shift_id,
        operator_id=shift.operator_id,
        machine_id=shift.machine_id,
        pin_verifier=pin_hash,
        expires_at=expires_at,
        signature=issue_offline_credential(
            shift.shift_id, shift.operator_id, shift.machine_id, pin_hash, expires_at
        ),
    )
    session.add(credential)
    await session.flush()
    return credential
