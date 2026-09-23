"""System and simulator control routes.

Available only when APP_ENV is dev or demo.
Covers connectivity toggle, clock state, and a health check.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloud.audit.service import record_audit
from app.config.settings import Settings, get_settings
from app.core.clock import sim_now
from app.core.connectivity import is_cloud_reachable, set_cloud_reachable
from app.db.session import get_db
from app.shared.enums import AuditAction
from app.sync.worker import CLOUD_TO_EDGE, EDGE_TO_CLOUD, pending_count

router = APIRouter(prefix="/api/system", tags=["system"])


def _require_demo(settings: Settings = Depends(get_settings)) -> None:
    if not settings.demo_routes_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    sim_time: str
    cloud_reachable: bool


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Public health check. Always available."""
    return HealthResponse(
        status="ok",
        sim_time=sim_now().isoformat(),
        cloud_reachable=is_cloud_reachable(),
    )


# ---------------------------------------------------------------------------
# Status (dev / demo only)
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: str
    sim_time: str
    cloud_reachable: bool
    app_env: str
    # Messages waiting in each outbox (queued while the cloud link is cut).
    cloud_outbox_pending: int
    edge_outbox_pending: int


@router.get("/status", response_model=StatusResponse, dependencies=[Depends(_require_demo)])
async def system_status(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db),
) -> StatusResponse:
    """Return current system state including connectivity, clock, and sync backlog."""
    return StatusResponse(
        status="ok",
        sim_time=sim_now().isoformat(),
        cloud_reachable=is_cloud_reachable(),
        app_env=settings.app_env,
        cloud_outbox_pending=await pending_count(session, CLOUD_TO_EDGE),
        edge_outbox_pending=await pending_count(session, EDGE_TO_CLOUD),
    )


# ---------------------------------------------------------------------------
# Connectivity toggle (dev / demo only)
# ---------------------------------------------------------------------------


class ConnectivityRequest(BaseModel):
    cloud_reachable: bool


class ConnectivityResponse(BaseModel):
    cloud_reachable: bool


@router.post(
    "/connectivity",
    response_model=ConnectivityResponse,
    dependencies=[Depends(_require_demo)],
)
async def set_connectivity(
    body: ConnectivityRequest,
    session: AsyncSession = Depends(get_db),
) -> ConnectivityResponse:
    """Toggle the cloud connectivity flag for demo purposes.

    The demo control has no signed-in user, so the audit entry is a system action.
    """
    set_cloud_reachable(body.cloud_reachable)
    record_audit(
        session, None, AuditAction.CONNECTIVITY_TOGGLE, "connectivity", "online" if body.cloud_reachable else "offline"
    )
    await session.commit()
    return ConnectivityResponse(cloud_reachable=is_cloud_reachable())
