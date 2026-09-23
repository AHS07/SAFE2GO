"""Offline login at the edge.

The operator gives username and PIN. The edge finds that operator in its
synced roster, takes the credential of their current shift, and checks
signature, sim-time expiry, machine, and PIN. No cloud call and no
password data are involved.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.core.errors import UnauthorizedError
from app.core.security import verify_offline_credential
from app.db.models.edge.assignment import EdgeOfflineCredential, EdgeOperator
from app.edge.shift import current_shift_for_operator

log = logging.getLogger("safe2go.offline_login")

_REJECTED = "Username or PIN is not correct, or there is no offline sign-in for your current shift."


@dataclass(frozen=True)
class OfflineIdentity:
    user_id: str
    operator_id: str
    shift_id: str
    machine_id: str


async def verify_offline_login(session: AsyncSession, username: str, pin: str) -> OfflineIdentity:
    operator = (
        await session.execute(select(EdgeOperator).where(EdgeOperator.username == username))
    ).scalar_one_or_none()
    if operator is None or operator.user_id is None:
        raise UnauthorizedError(_REJECTED)

    shift = await current_shift_for_operator(session, operator.operator_id)
    credential = None
    if shift is not None:
        credential = (
            await session.execute(
                select(EdgeOfflineCredential).where(EdgeOfflineCredential.shift_id == shift.shift_id)
            )
        ).scalar_one_or_none()
    if shift is None or credential is None:
        log.info("Offline login refused, no credential", extra={"operator_id": operator.operator_id})
        raise UnauthorizedError(_REJECTED)

    payload = verify_offline_credential(credential.signature, shift.machine_id, pin, sim_now())
    if payload.get("operator_id") != operator.operator_id or payload.get("shift_id") != shift.shift_id:
        raise UnauthorizedError(_REJECTED)
    return OfflineIdentity(
        user_id=operator.user_id,
        operator_id=operator.operator_id,
        shift_id=shift.shift_id,
        machine_id=shift.machine_id,
    )
