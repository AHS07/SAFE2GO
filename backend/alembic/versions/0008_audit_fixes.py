"""Audit fixes: proximity sensor health, sync dead letters, ETA revision reason.

- edge.telemetry.proximity_sensor_ok: False means the proximity reading is
  unknown (sensor fault), not "nothing in range". History had working sensors.
- outbox first_failed_at (real time) and a sync_dead_letter table per tier:
  a message that keeps failing for SYNC_DEAD_LETTER_AFTER_SECONDS moves out
  of the outbox so later messages continue; the admin can retry it.
- task.revision_reason (edge and cloud): why the ETA was last revised.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE edge.telemetry ADD COLUMN proximity_sensor_ok BOOLEAN NOT NULL DEFAULT TRUE")
    for schema in ("cloud", "edge"):
        op.execute(f"ALTER TABLE {schema}.{schema}_outbox ADD COLUMN first_failed_at TIMESTAMPTZ")
        op.execute(f"""
            CREATE TABLE {schema}.sync_dead_letter (
                message_id VARCHAR(36) PRIMARY KEY,
                idempotency_key VARCHAR(100) NOT NULL UNIQUE,
                message_type VARCHAR(50) NOT NULL,
                entity_id VARCHAR(36),
                payload TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                attempts INTEGER NOT NULL,
                last_error VARCHAR(500),
                first_failed_at TIMESTAMPTZ,
                dead_at TIMESTAMPTZ NOT NULL
            )
        """)
        op.execute(f"ALTER TABLE {schema}.task ADD COLUMN revision_reason VARCHAR(20)")


def downgrade() -> None:
    for schema in ("cloud", "edge"):
        op.execute(f"ALTER TABLE {schema}.task DROP COLUMN revision_reason")
        op.execute(f"DROP TABLE IF EXISTS {schema}.sync_dead_letter")
        op.execute(f"ALTER TABLE {schema}.{schema}_outbox DROP COLUMN first_failed_at")
    op.execute("ALTER TABLE edge.telemetry DROP COLUMN proximity_sensor_ok")
