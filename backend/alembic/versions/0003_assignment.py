"""Assignment support: unknown actual weather on new shifts, system audit entries.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE cloud.shift ALTER COLUMN weather_actual DROP NOT NULL")
    op.execute("ALTER TABLE cloud.audit_log ALTER COLUMN user_id DROP NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shift_operator_start ON cloud.shift (operator_id, scheduled_start)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shift_machine_start ON cloud.shift (machine_id, scheduled_start)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_task_shift ON cloud.task (shift_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cloud.ix_task_shift")
    op.execute("DROP INDEX IF EXISTS cloud.ix_shift_machine_start")
    op.execute("DROP INDEX IF EXISTS cloud.ix_shift_operator_start")
    op.execute("DELETE FROM cloud.audit_log WHERE user_id IS NULL")
    op.execute("ALTER TABLE cloud.audit_log ALTER COLUMN user_id SET NOT NULL")
    op.execute("UPDATE cloud.shift SET weather_actual = weather_forecast WHERE weather_actual IS NULL")
    op.execute("ALTER TABLE cloud.shift ALTER COLUMN weather_actual SET NOT NULL")
