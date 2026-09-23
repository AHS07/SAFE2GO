"""FastAPI application factory."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, assistant, emergency, simulator, system, training
from app.api.routes.auth import router as auth_router
from app.api.routes.operator import router as operator_router
from app.config.settings import get_settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import setup_logging
from app.core.request_context import REQUEST_ID_HEADER, RequestIdMiddleware
from app.edge.assistant.retrieval import get_manual_index
from app.shared.eta_model import get_predictor
from app.sync.runtime import sync_worker

logger = logging.getLogger("safe2go.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load the ETA artifact once. A missing or broken artifact means
    # historical-average estimates, never a startup failure.
    get_predictor()
    try:
        get_manual_index()
    except Exception as exc:
        # Manual search reports the failure per request; nothing else depends on it.
        logger.error("Manual index failed to build", exc_info=exc)
    sync_worker.start()
    yield
    await sync_worker.stop()
    if get_settings().demo_routes_enabled:
        from app.api.routes.simulator import shutdown_simulator

        await shutdown_simulator()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(level="DEBUG" if settings.app_env == "dev" else "INFO")

    app = FastAPI(
        title="SAFE2GO",
        description="Operator assistant for CAT construction machines.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.demo_routes_enabled else None,
        redoc_url="/redoc" if settings.demo_routes_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last so it wraps everything, including CORS responses.
    app.add_middleware(RequestIdMiddleware)

    register_error_handlers(app)

    # Auth and operator routes
    app.include_router(auth_router)
    app.include_router(operator_router)

    # Training hub, manual retrieval, emergency guidance
    app.include_router(training.router)
    app.include_router(assistant.router)
    app.include_router(emergency.router)

    # System routes (health always available; status/connectivity are dev/demo only)
    app.include_router(system.router)
    app.include_router(admin.router)

    if settings.demo_routes_enabled:
        app.include_router(simulator.router)

    # WebSocket
    from app.api.ws.operator_ws import create_ws_router
    from app.db.session import _session_factory
    ws_router = create_ws_router(_session_factory)
    app.include_router(ws_router)

    logger.info("SAFE2GO started", extra={"app_env": settings.app_env})
    return app


app = create_app()
