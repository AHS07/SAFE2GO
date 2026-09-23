"""Edge training cache, edge quiz results, recommendation dedup constraint.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE edge.training_module (
            module_id VARCHAR(36) PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            format VARCHAR(50) NOT NULL,
            duration_min INTEGER NOT NULL,
            content_path VARCHAR(300)
        )
    """)

    op.execute("""
        CREATE TABLE edge.anomaly_training_map (
            map_id VARCHAR(36) PRIMARY KEY,
            source_type VARCHAR(20) NOT NULL,
            type_value VARCHAR(50) NOT NULL,
            recommended_module_id VARCHAR(36) NOT NULL,
            CONSTRAINT uq_edge_training_map_source UNIQUE (source_type, type_value)
        )
    """)

    op.execute("""
        CREATE TABLE edge.quiz_result (
            result_id VARCHAR(36) PRIMARY KEY,
            operator_id VARCHAR(36) NOT NULL,
            module_id VARCHAR(36) NOT NULL,
            score FLOAT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_edge_quiz_result_operator_id ON edge.quiz_result (operator_id)")

    # One recommendation per module per operator per shift (prd.md F5.4).
    op.execute("""
        ALTER TABLE edge.recommendation
        ADD CONSTRAINT uq_recommendation_operator_shift_module
        UNIQUE (operator_id, shift_id, module_id)
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE edge.recommendation "
        "DROP CONSTRAINT IF EXISTS uq_recommendation_operator_shift_module"
    )
    op.execute("DROP TABLE IF EXISTS edge.quiz_result")
    op.execute("DROP TABLE IF EXISTS edge.anomaly_training_map")
    op.execute("DROP TABLE IF EXISTS edge.training_module")
