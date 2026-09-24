"""Telemetry streaming pipeline: edge spool and cloud analytics tables.

- edge.stream_spool: records waiting to be forwarded to Kafka. Only live
  ticks and safety events enter it (never the generated history). A row is
  deleted once Kafka acknowledges it. seq is the send order.
- cloud.telemetry_archive: raw ticks received from Kafka, one row per
  event_id, so duplicate deliveries are ignored.
- cloud.telemetry_gap: raw ticks dropped from a full edge spool, by range.
- cloud.stream_event: incidents and behavior events received from Kafka.
- cloud.fleet_minute_rollup: per-machine, per-minute totals, recomputed
  from the archive, so they stay correct under duplicates and reordering.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE edge.stream_spool (
            seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id VARCHAR(36) NOT NULL UNIQUE,
            kind VARCHAR(20) NOT NULL,
            priority SMALLINT NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            spooled_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_stream_spool_send_order ON edge.stream_spool (priority, seq)")
    op.execute("CREATE INDEX ix_stream_spool_machine ON edge.stream_spool (machine_id, seq)")

    op.execute("""
        CREATE TABLE cloud.telemetry_archive (
            event_id VARCHAR(36) PRIMARY KEY,
            edge_seq BIGINT NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            shift_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            ts TIMESTAMPTZ NOT NULL,
            engine_running BOOLEAN NOT NULL,
            engine_rpm DOUBLE PRECISION NOT NULL,
            hydraulic_active BOOLEAN NOT NULL,
            machine_speed DOUBLE PRECISION NOT NULL,
            fuel_used DOUBLE PRECISION NOT NULL,
            load_cycles INTEGER NOT NULL,
            payload_pct DOUBLE PRECISION NOT NULL,
            received_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_telemetry_archive_machine_ts ON cloud.telemetry_archive (machine_id, ts)")

    op.execute("""
        CREATE TABLE cloud.telemetry_gap (
            event_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL,
            from_ts TIMESTAMPTZ NOT NULL,
            to_ts TIMESTAMPTZ NOT NULL,
            dropped_count INTEGER NOT NULL,
            dropped_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.stream_event (
            event_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL,
            kind VARCHAR(20) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            severity VARCHAR(20),
            ts TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL,
            received_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_stream_event_machine_ts ON cloud.stream_event (machine_id, ts)")

    op.execute("""
        CREATE TABLE cloud.fleet_minute_rollup (
            machine_id VARCHAR(36) NOT NULL,
            bucket_start TIMESTAMPTZ NOT NULL,
            ticks INTEGER NOT NULL,
            engine_on_ticks INTEGER NOT NULL,
            working_ticks INTEGER NOT NULL,
            idle_ticks INTEGER NOT NULL,
            avg_rpm DOUBLE PRECISION NOT NULL,
            fuel_used DOUBLE PRECISION NOT NULL,
            load_cycles INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (machine_id, bucket_start)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE cloud.fleet_minute_rollup")
    op.execute("DROP TABLE cloud.stream_event")
    op.execute("DROP TABLE cloud.telemetry_gap")
    op.execute("DROP TABLE cloud.telemetry_archive")
    op.execute("DROP TABLE edge.stream_spool")
