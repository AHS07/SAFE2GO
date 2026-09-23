"""The application's single sync worker, started and stopped by the app lifespan."""
from __future__ import annotations

from app.db.session import _session_factory
from app.sync.worker import SyncWorker

sync_worker = SyncWorker(_session_factory)
