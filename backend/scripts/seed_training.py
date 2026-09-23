"""Publish the training catalog and send it to the edge.

Run after editing files in content/training/:
    python -m scripts.seed_training
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cloud.publish import queue_training_content
from app.cloud.training.catalog import publish_catalog
from app.config.settings import get_settings
from app.core.logging import setup_logging
from app.sync.worker import deliver_all

log = logging.getLogger("safe2go.seed_training")


async def run() -> None:
    setup_logging("INFO")
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        await publish_catalog(session)
        await queue_training_content(session)
        await session.commit()

    await deliver_all(session_factory)
    await engine.dispose()
    log.info("Training content published")


if __name__ == "__main__":
    asyncio.run(run())
