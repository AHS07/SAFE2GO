"""The application's streaming workers, started by the app lifespan when KAFKA_ENABLED is true.

The edge forwarder and the cloud consumer run in the one demo process, as
the sync worker does for both sync directions.
"""
from __future__ import annotations

import logging

from app.cloud.stream.consumer import AnalyticsConsumer
from app.config.settings import get_settings
from app.db.session import _session_factory
from app.edge.stream.forwarder import ForwarderState, StreamForwarder

log = logging.getLogger("safe2go.stream")

forwarder = StreamForwarder(_session_factory)
consumer = AnalyticsConsumer(_session_factory)


def start_streaming() -> None:
    settings = get_settings()
    if not settings.kafka_enabled:
        forwarder.stats.state = ForwarderState.DISABLED
        return
    forwarder.start()
    consumer.start()
    log.info("Telemetry streaming started", extra={"bootstrap": settings.kafka_bootstrap_servers})


async def stop_streaming() -> None:
    await forwarder.stop()
    await consumer.stop()
    if not get_settings().kafka_enabled:
        forwarder.stats.state = ForwarderState.DISABLED
