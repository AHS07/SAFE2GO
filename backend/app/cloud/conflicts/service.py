"""Sync conflicts for the admin.

Conflicts are created by the edge-to-cloud task_status handler when the
cloud had changed a task the edge had already started. The rollback is
automatic; the admin reviews the record and marks it resolved.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.audit.service import record_audit
from app.core.errors import NotFoundError
from app.db.models.cloud.analytics import SyncConflict
from app.shared.enums import AuditAction


async def list_conflicts(session: AsyncSession, include_resolved: bool) -> list[SyncConflict]:
    query = select(SyncConflict).order_by(SyncConflict.created_at.desc())
    if not include_resolved:
        query = query.where(SyncConflict.resolved.is_(False))
    return list((await session.execute(query)).scalars().all())


async def resolve_conflict(session: AsyncSession, conflict_id: str, actor_id: str | None) -> SyncConflict:
    conflict = await session.get(SyncConflict, conflict_id)
    if conflict is None:
        raise NotFoundError(f"Conflict {conflict_id} not found.", details={"conflict_id": conflict_id})
    conflict.resolved = True
    await session.flush()
    record_audit(session, actor_id, AuditAction.CONFLICT_RESOLVE, "sync_conflict", conflict_id)
    return conflict
