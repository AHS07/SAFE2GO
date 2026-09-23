"""Outbox writing and payload encoding, shared by both tiers.

Each tier writes messages to its own outbox table in the same transaction
as the change they describe. The worker delivers them later, in order,
only while cloud_reachable is true.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import sim_now
from app.db.models.cloud.sync import CloudOutbox
from app.db.models.edge.sync import EdgeOutbox
from app.shared.enums import SyncMessageType
from app.sync.fields import DATETIME_FIELDS

Outbox = type[CloudOutbox] | type[EdgeOutbox]


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__} in a sync payload")


def encode_payload(payload: dict) -> str:
    return json.dumps(payload, default=_encode)


def decode_payload(raw: str) -> dict:
    return json.loads(raw)


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def row_dict(row: Any, fields: tuple[str, ...]) -> dict:
    return {name: getattr(row, name) for name in fields}


def payload_values(payload: dict, fields: tuple[str, ...]) -> dict:
    """The listed fields from a decoded payload, with timestamps parsed."""
    return {
        name: parse_dt(payload.get(name)) if name in DATETIME_FIELDS else payload.get(name)
        for name in fields
    }


def enqueue(
    session: AsyncSession,
    outbox: Outbox,
    message_type: SyncMessageType,
    payload: dict,
    entity_id: str | None = None,
) -> None:
    """Add one message to the outbox. The caller's commit makes it durable."""
    session.add(outbox(
        message_id=str(uuid.uuid4()),
        idempotency_key=f"{message_type.value}:{uuid.uuid4().hex}",
        message_type=message_type.value,
        entity_id=entity_id,
        payload=encode_payload(payload),
        created_at=sim_now(),
        attempts=0,
    ))
