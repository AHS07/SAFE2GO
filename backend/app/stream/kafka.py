"""Kafka clients and topics.

Imported only by the forwarder and the consumer, which run only when
KAFKA_ENABLED is true. Nothing on the tick or safety path imports this.
"""
from __future__ import annotations

import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from app.config.settings import Settings

log = logging.getLogger("safe2go.stream.kafka")

_REQUEST_TIMEOUT_MS = 5_000
# CreateTopics per-topic error codes that mean the topic is there.
_TOPIC_OK = (0, 36)  # none, TOPIC_ALREADY_EXISTS (created concurrently)


def make_producer(settings: Settings) -> AIOKafkaProducer:
    """acks=all plus idempotence: a record counts as sent only once the broker
    has it, and producer-side retries never duplicate or reorder a partition."""
    return AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=settings.kafka_client_id,
        acks="all",
        enable_idempotence=True,
        request_timeout_ms=_REQUEST_TIMEOUT_MS,
        retry_backoff_ms=200,
        linger_ms=5,
    )


def make_consumer(settings: Settings) -> AIOKafkaConsumer:
    """Offsets are committed by the consumer only after the database commit."""
    return AIOKafkaConsumer(
        settings.kafka_topic_telemetry,
        settings.kafka_topic_events,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=f"{settings.kafka_consumer_group}-client",
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        request_timeout_ms=_REQUEST_TIMEOUT_MS * 4,
        retry_backoff_ms=200,
    )


async def ensure_topics(settings: Settings) -> None:
    """Create the topics if they are missing. Safe to call repeatedly."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=f"{settings.kafka_client_id}-admin",
        request_timeout_ms=_REQUEST_TIMEOUT_MS,
    )
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        retention_ms = str(settings.kafka_retention_hours * 3_600_000)
        missing = [
            NewTopic(
                name=name,
                num_partitions=settings.kafka_topic_partitions,
                replication_factor=1,
                topic_configs={"retention.ms": retention_ms},
            )
            for name in (settings.kafka_topic_telemetry, settings.kafka_topic_events)
            if name not in existing
        ]
        if missing:
            response = await admin.create_topics(missing)
            for name, code, *_ in getattr(response, "topic_errors", []):
                if code not in _TOPIC_OK:
                    raise RuntimeError(f"Could not create Kafka topic {name} (error code {code}).")
            log.info("Kafka topics created", extra={"topics": [t.name for t in missing]})
    finally:
        await admin.close()
