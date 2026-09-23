"""SQLAlchemy declarative base.

All ORM models import Base from here so Alembic can discover them
via the single metadata object.

The wildcard import at the bottom registers every model against the
metadata before alembic/env.py calls Base.metadata.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Register all models — must come after Base is defined.
import app.db.models  # noqa: E402, F401
