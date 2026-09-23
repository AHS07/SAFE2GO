"""Emergency guidance route (prd.md F8).

Static, preloaded content served from the edge. Needs no login so it is
reachable from every screen, including the login screen, and offline.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.shared.content import EmergencyGuide, load_emergency_guide

router = APIRouter(prefix="/api/emergency", tags=["emergency"])


@router.get("", response_model=EmergencyGuide)
async def get_emergency_guidance() -> EmergencyGuide:
    return load_emergency_guide()
