"""Rename seeded machine models to neutral codes.

Model names are picked by position, not by a random draw, so this rename
gives the same data as regenerating with the updated master data.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMES: list[tuple[str, str]] = [
    ("CAT 320", "EX-20"),
    ("CAT 336", "EX-36"),
    ("CAT 390F", "EX-90"),
    ("CAT 950", "WL-18"),
    ("CAT 966", "WL-24"),
    ("CAT 980", "WL-30"),
    ("CAT 420", "BL-7"),
    ("CAT 432", "BL-8"),
    ("CAT 450", "BL-9"),
]

_RENAME_SQL = sa.text("UPDATE cloud.machine SET machine_model = :new WHERE machine_model = :old")


def upgrade() -> None:
    for old, new in _RENAMES:
        op.execute(_RENAME_SQL.bindparams(old=old, new=new))


def downgrade() -> None:
    for old, new in _RENAMES:
        op.execute(_RENAME_SQL.bindparams(old=new, new=old))
