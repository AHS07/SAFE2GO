"""Machine hour meter for maintenance suggestions.

engine_hours is the current meter: the last service reading plus the engine
hours of every summarized shift on the machine. The generator computes the
same value, so regenerated data matches.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUR_METER_SQL = """
    UPDATE cloud.machine AS m
    SET engine_hours = m.last_service_engine_hours + COALESCE((
        SELECT SUM(ss.engine_hours)
        FROM cloud.shift_summary ss
        JOIN cloud.shift s ON s.shift_id = ss.shift_id
        WHERE s.machine_id = m.machine_id
    ), 0)
"""


def upgrade() -> None:
    op.execute("ALTER TABLE cloud.machine ADD COLUMN engine_hours DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute(HOUR_METER_SQL)
    op.execute("ALTER TABLE edge.machine ADD COLUMN engine_hours DOUBLE PRECISION NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE edge.machine DROP COLUMN engine_hours")
    op.execute("ALTER TABLE cloud.machine DROP COLUMN engine_hours")
