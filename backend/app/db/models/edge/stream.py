"""Edge schema: spool of records waiting to be forwarded to Kafka."""
from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, Identity, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StreamSpool(Base):
    """One record for Kafka. Deleted only after the broker acknowledges it.

    Only live ticks and safety events are spooled, never generated history.
    event_id is stable across resends, so consumers can drop duplicates.
    """

    __tablename__ = "stream_spool"
    __table_args__ = {"schema": "edge"}

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Lower sends first: gap markers, then safety events, then raw ticks.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_time: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)  # sim time
    spooled_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)  # real time
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
