"""Cloud schema: Operator master data and qualifications."""
from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.enums import SkillLevel


class Operator(Base):
    __tablename__ = "operator"
    __table_args__ = {"schema": "cloud"}

    operator_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_name: Mapped[str] = mapped_column(String(200), nullable=False)


class OperatorQualification(Base):
    __tablename__ = "operator_qualification"
    __table_args__ = {"schema": "cloud"}

    operator_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud.operator.operator_id", ondelete="CASCADE"), primary_key=True
    )
    machine_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_level: Mapped[str] = mapped_column(
        Enum(SkillLevel, schema="cloud", name="skilllevel", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
