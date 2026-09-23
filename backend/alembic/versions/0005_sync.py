"""Sync: edge copies of cloud records, inboxes, outbox ordering, cloud copies of edge records, PIN.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EDGE_TABLES = [
    "machine",
    "operator",
    "operator_qualification",
    "shift",
    "task",
    "behavior_baseline",
    "eta_aggregate",
    "offline_credential",
    "sync_inbox",
]
_CLOUD_TABLES = ["incident", "behavior_event", "sync_inbox"]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE edge.machine (
            machine_id VARCHAR(36) PRIMARY KEY,
            machine_model VARCHAR(100) NOT NULL,
            machine_type VARCHAR(50) NOT NULL,
            machine_age INTEGER NOT NULL,
            machine_status VARCHAR(20) NOT NULL,
            service_interval_hours DOUBLE PRECISION NOT NULL,
            last_service_engine_hours DOUBLE PRECISION NOT NULL,
            bucket_capacity DOUBLE PRECISION NOT NULL,
            tilt_limit_degrees DOUBLE PRECISION NOT NULL,
            rated_max_rpm DOUBLE PRECISION NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE edge.operator (
            operator_id VARCHAR(36) PRIMARY KEY,
            operator_name VARCHAR(200) NOT NULL,
            user_id VARCHAR(36),
            username VARCHAR(100) UNIQUE
        )
    """)
    op.execute("""
        CREATE TABLE edge.operator_qualification (
            operator_id VARCHAR(36) NOT NULL,
            machine_type VARCHAR(50) NOT NULL,
            skill_level VARCHAR(20) NOT NULL,
            PRIMARY KEY (operator_id, machine_type)
        )
    """)
    op.execute("""
        CREATE TABLE edge.shift (
            shift_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            date TIMESTAMPTZ NOT NULL,
            scheduled_start TIMESTAMPTZ NOT NULL,
            scheduled_end TIMESTAMPTZ NOT NULL,
            weather_forecast VARCHAR(20) NOT NULL,
            weather_actual VARCHAR(20),
            sync_seq BIGINT NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX ix_edge_shift_machine_id ON edge.shift (machine_id)")
    op.execute("CREATE INDEX ix_edge_shift_operator_id ON edge.shift (operator_id)")
    op.execute("""
        CREATE TABLE edge.task (
            task_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL,
            task_type VARCHAR(50) NOT NULL,
            operator_skill_at_assignment VARCHAR(20) NOT NULL,
            target_quantity DOUBLE PRECISION NOT NULL,
            quantity_unit VARCHAR(20) NOT NULL,
            material_type VARCHAR(100) NOT NULL,
            completed_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL,
            scheduled_start TIMESTAMPTZ NOT NULL,
            scheduled_end TIMESTAMPTZ,
            reassigned_at TIMESTAMPTZ,
            actual_start TIMESTAMPTZ,
            actual_end TIMESTAMPTZ,
            paused_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,
            blocked_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,
            raw_predicted_time DOUBLE PRECISION,
            planning_eta DOUBLE PRECISION,
            revised_predicted_time DOUBLE PRECISION,
            revised_at TIMESTAMPTZ,
            model_version VARCHAR(50),
            aggregates_version VARCHAR(50),
            is_fallback BOOLEAN NOT NULL DEFAULT FALSE,
            sync_seq BIGINT NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX ix_edge_task_shift_id ON edge.task (shift_id)")
    op.execute("""
        CREATE TABLE edge.behavior_baseline (
            baseline_id VARCHAR(36) PRIMARY KEY,
            scope VARCHAR(20) NOT NULL,
            scope_id VARCHAR(36),
            machine_type VARCHAR(50) NOT NULL,
            metric VARCHAR(50) NOT NULL,
            median DOUBLE PRECISION NOT NULL,
            mad DOUBLE PRECISION NOT NULL,
            sample_count INTEGER NOT NULL,
            version VARCHAR(50) NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE edge.eta_aggregate (
            aggregate_id VARCHAR(36) PRIMARY KEY,
            scope VARCHAR(20) NOT NULL,
            scope_id VARCHAR(36),
            task_type VARCHAR(50) NOT NULL,
            avg_minutes_per_unit DOUBLE PRECISION NOT NULL,
            avg_idle_ratio DOUBLE PRECISION NOT NULL,
            avg_cycle_rate DOUBLE PRECISION NOT NULL,
            version VARCHAR(50) NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE edge.offline_credential (
            credential_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL UNIQUE,
            operator_id VARCHAR(36) NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            signature TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_edge_offline_credential_operator_id ON edge.offline_credential (operator_id)")

    for schema in ("edge", "cloud"):
        op.execute(f"""
            CREATE TABLE {schema}.sync_inbox (
                idempotency_key VARCHAR(100) PRIMARY KEY,
                message_type VARCHAR(50) NOT NULL,
                seq BIGINT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL
            )
        """)
        op.execute(f"CREATE INDEX ix_{schema}_sync_inbox_type ON {schema}.sync_inbox (message_type)")
        outbox = f"{schema}.{schema}_outbox"
        op.execute(f"ALTER TABLE {outbox} ADD COLUMN seq BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE")
        op.execute(f"ALTER TABLE {outbox} ADD COLUMN entity_id VARCHAR(36)")
        op.execute(f"CREATE INDEX ix_{schema}_outbox_entity ON {outbox} (entity_id)")
        op.execute(f"CREATE INDEX ix_{schema}_outbox_pending ON {outbox} (created_at, seq) WHERE delivered_at IS NULL")

    op.execute("""
        CREATE TABLE cloud.incident (
            incident_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            event_start TIMESTAMPTZ NOT NULL,
            event_end TIMESTAMPTZ,
            incident_type VARCHAR(30) NOT NULL,
            peak_severity VARCHAR(20) NOT NULL,
            escalation_reason VARCHAR(20),
            status VARCHAR(20) NOT NULL,
            source VARCHAR(20) NOT NULL,
            description TEXT,
            acknowledged_at TIMESTAMPTZ,
            acknowledged_by VARCHAR(36),
            sync_seq BIGINT NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX ix_cloud_incident_shift_id ON cloud.incident (shift_id)")
    op.execute("""
        CREATE TABLE cloud.behavior_event (
            event_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            shift_id VARCHAR(36) NOT NULL,
            event_type VARCHAR(40) NOT NULL,
            start TIMESTAMPTZ NOT NULL,
            "end" TIMESTAMPTZ NOT NULL,
            magnitude DOUBLE PRECISION NOT NULL,
            baseline_value DOUBLE PRECISION
        )
    """)
    op.execute("CREATE INDEX ix_cloud_behavior_event_shift_id ON cloud.behavior_event (shift_id)")

    op.execute("ALTER TABLE cloud.task ADD COLUMN edge_sync_seq BIGINT NOT NULL DEFAULT 0")
    op.execute('ALTER TABLE cloud."user" ADD COLUMN pin_hash VARCHAR(256)')


def downgrade() -> None:
    op.execute('ALTER TABLE cloud."user" DROP COLUMN pin_hash')
    op.execute("ALTER TABLE cloud.task DROP COLUMN edge_sync_seq")
    for schema in ("edge", "cloud"):
        outbox = f"{schema}.{schema}_outbox"
        op.execute(f"DROP INDEX IF EXISTS {schema}.ix_{schema}_outbox_pending")
        op.execute(f"DROP INDEX IF EXISTS {schema}.ix_{schema}_outbox_entity")
        op.execute(f"ALTER TABLE {outbox} DROP COLUMN entity_id")
        op.execute(f"ALTER TABLE {outbox} DROP COLUMN seq")
    for table in _CLOUD_TABLES:
        op.execute(f"DROP TABLE IF EXISTS cloud.{table}")
    for table in _EDGE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS edge.{table}")
