"""Keep original ordering when retrying sync dead letters.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for schema in ("cloud", "edge"):
        op.execute(f"ALTER TABLE {schema}.{schema}_outbox ADD COLUMN replay_seq BIGINT")
        op.execute(f"ALTER TABLE {schema}.sync_dead_letter ADD COLUMN seq BIGINT NOT NULL DEFAULT 0")
        op.execute(f"ALTER TABLE {schema}.sync_dead_letter ALTER COLUMN seq DROP DEFAULT")


def downgrade() -> None:
    for schema in ("cloud", "edge"):
        op.execute(f"ALTER TABLE {schema}.sync_dead_letter DROP COLUMN seq")
        op.execute(f"ALTER TABLE {schema}.{schema}_outbox DROP COLUMN replay_seq")
