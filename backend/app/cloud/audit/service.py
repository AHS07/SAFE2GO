"""Cloud audit log writer.

Entries are added to the caller's session so they commit, or roll back,
together with the action they record. Edge actions arrive through sync in
phase 8 with tier = edge.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.db.models.cloud.user import AuditLog
from app.shared.enums import AuditAction, Tier


def record_audit(
    session: AsyncSession,
    user_id: str | None,
    action: AuditAction,
    target_entity: str | None = None,
    target_id: str | None = None,
    tier: Tier = Tier.CLOUD,
) -> AuditLog:
    """Add an audit entry. user_id is None for system actions such as the demo connectivity toggle."""
    entry = AuditLog(
        log_id=str(uuid.uuid4()),
        user_id=user_id,
        action=action.value,
        target_entity=target_entity,
        target_id=target_id,
        timestamp=sim_now(),
        tier=tier.value,
    )
    session.add(entry)
    return entry
