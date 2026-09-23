"""Cloud schema: Users, offline credentials, and audit log."""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import Tier, UserRole


class User(Base):
    __tablename__ = "user"
    __table_args__ = {"schema": "cloud"}

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    role: Mapped[str] = mapped_column(
        Enum(UserRole, schema="cloud", name="userrole", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    linked_operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cloud.operator.operator_id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # Operator PIN for offline login. Only its verifier leaves the cloud, inside a shift credential.
    pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)


class OfflineCredential(Base):
    __tablename__ = "offline_credential"
    __table_args__ = {"schema": "cloud"}

    credential_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.shift.shift_id", ondelete="CASCADE"), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.operator.operator_id"), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud.machine.machine_id"), nullable=False)
    pin_verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "cloud"}

    log_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # None for system actions (demo connectivity toggle).
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cloud.user.user_id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_entity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    tier: Mapped[str] = mapped_column(
        Enum(Tier, schema="cloud", name="tier", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
