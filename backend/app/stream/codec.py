"""Kafka record format shared by the edge forwarder and the cloud consumer.

Every record is one JSON envelope:

    {"v": 1, "event_id": ..., "kind": ..., "edge_seq": ..., "machine_id": ...,
     "event_time": ..., "spooled_at": ..., "data": {...}}

The key is the machine id, so all records of one machine land on one
partition in send order. event_id is stable across resends; consumers
drop duplicates by it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.shared.enums import StreamKind

VERSION = 1


class MalformedRecord(ValueError):
    """A record that cannot be decoded. Skipped and counted, never retried."""


@dataclass(frozen=True)
class Envelope:
    event_id: str
    kind: str
    edge_seq: int
    machine_id: str
    event_time: datetime
    spooled_at: datetime
    data: dict


def topic_for(kind: str, telemetry_topic: str, events_topic: str) -> str:
    """Gap markers travel with the ticks they describe; safety events have their own topic."""
    if kind in (StreamKind.TELEMETRY.value, StreamKind.GAP.value):
        return telemetry_topic
    return events_topic


def encode(envelope: Envelope) -> bytes:
    return json.dumps({
        "v": VERSION,
        "event_id": envelope.event_id,
        "kind": envelope.kind,
        "edge_seq": envelope.edge_seq,
        "machine_id": envelope.machine_id,
        "event_time": envelope.event_time.isoformat(),
        "spooled_at": envelope.spooled_at.isoformat(),
        "data": envelope.data,
    }, separators=(",", ":")).encode()


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(value)  # type: ignore[arg-type] - TypeError is handled by decode
    if parsed.tzinfo is None:
        raise MalformedRecord("Timestamps must carry a UTC offset.")
    return parsed


def decode(raw: bytes | None) -> Envelope:
    try:
        body = json.loads(raw or b"")
        if body.get("v") != VERSION:
            raise MalformedRecord(f"Unsupported record version {body.get('v')!r}.")
        kind = body["kind"]
        if kind not in {k.value for k in StreamKind}:
            raise MalformedRecord(f"Unknown record kind {kind!r}.")
        return Envelope(
            event_id=str(body["event_id"]),
            kind=kind,
            edge_seq=int(body["edge_seq"]),
            machine_id=str(body["machine_id"]),
            event_time=_timestamp(body["event_time"]),
            spooled_at=_timestamp(body["spooled_at"]),
            data=dict(body["data"]),
        )
    except MalformedRecord:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MalformedRecord(str(exc)[:200]) from exc
