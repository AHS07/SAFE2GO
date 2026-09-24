"""Cloud schema: outbox for cloud-to-edge messages, inbox for applied edge messages."""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, DateTime, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CloudOutbox(Base):
    __tablename__ = "cloud_outbox"
    __table_args__ = {"schema": "cloud"}

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Insertion order; also the version stamped on rows the message updates.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[str] = mapped_column(String, nullable=False)   # JSON text
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Real time of the first failed attempt; decides when the message is dead-lettered.
    first_failed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Original sequence used when replaying a dead-letter message.
    replay_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class CloudInbox(Base):
    """Idempotency keys of edge messages already applied in the cloud."""

    __tablename__ = "sync_inbox"
    __table_args__ = {"schema": "cloud"}

    idempotency_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class CloudDeadLetter(Base):
    """A message that kept failing and was moved out of the outbox, so later
    messages continue. The admin can list and retry it."""

    __tablename__ = "sync_dead_letter"
    __table_args__ = {"schema": "cloud"}

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    first_failed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
