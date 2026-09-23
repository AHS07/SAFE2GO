"""Initial schema: cloud and edge schemas with all tables.

Revision ID: 0001
Revises:
Create Date: 2026-09-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Shorthand — all enum columns are stored as varchar in the table DDL.
# The actual PG enum types are created separately via raw SQL above.
_E = sa.String(50)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS cloud")
    op.execute("CREATE SCHEMA IF NOT EXISTS edge")

    # Cloud enums
    op.execute("CREATE TYPE cloud.machinetype AS ENUM ('excavator', 'wheel_loader', 'backhoe_loader')")
    op.execute("CREATE TYPE cloud.machinestatus AS ENUM ('available', 'maintenance')")
    op.execute("CREATE TYPE cloud.skilllevel AS ENUM ('beginner', 'intermediate', 'expert')")
    op.execute("CREATE TYPE cloud.tasktype AS ENUM ('excavation', 'material_loading', 'trenching')")
    op.execute("CREATE TYPE cloud.quantityunit AS ENUM ('m3', 'loads')")
    op.execute("CREATE TYPE cloud.taskstatus AS ENUM ('assigned', 'in_progress', 'paused', 'blocked', 'done', 'cancelled')")
    op.execute("CREATE TYPE cloud.weathercategory AS ENUM ('clear', 'cloudy', 'rain', 'fog', 'storm')")
    op.execute("CREATE TYPE cloud.userrole AS ENUM ('operator', 'admin')")
    op.execute("CREATE TYPE cloud.tier AS ENUM ('cloud', 'edge')")

    # Edge enums
    op.execute("CREATE TYPE edge.incidenttype AS ENUM ('seatbelt', 'operator_not_seated', 'proximity', 'overloading', 'tilt', 'working_condition', 'manual_report')")
    op.execute("CREATE TYPE edge.severity AS ENUM ('warning', 'critical')")
    op.execute("CREATE TYPE edge.incidentstatus AS ENUM ('open', 'acknowledged', 'resolved')")
    op.execute("CREATE TYPE edge.escalationreason AS ENUM ('grace_period', 'threshold', 'repeat')")
    op.execute("CREATE TYPE edge.behavioreventtype AS ENUM ('excessive_idling', 'repeated_overloading', 'high_rpm_travel', 'repeated_seatbelt_violation', 'high_idle_ratio')")

    # Use raw SQL for all tables to avoid SQLAlchemy auto-creating enum types
    op.execute("""
        CREATE TABLE cloud.machine (
            machine_id VARCHAR(36) PRIMARY KEY,
            machine_model VARCHAR(100) NOT NULL,
            machine_type cloud.machinetype NOT NULL,
            machine_age INTEGER NOT NULL,
            machine_status cloud.machinestatus NOT NULL DEFAULT 'available',
            service_interval_hours FLOAT NOT NULL,
            last_service_engine_hours FLOAT NOT NULL DEFAULT 0,
            bucket_capacity FLOAT NOT NULL,
            tilt_limit_degrees FLOAT NOT NULL,
            rated_max_rpm FLOAT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.operator (
            operator_id VARCHAR(36) PRIMARY KEY,
            operator_name VARCHAR(200) NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.operator_qualification (
            operator_id VARCHAR(36) REFERENCES cloud.operator(operator_id) ON DELETE CASCADE,
            machine_type VARCHAR(50) NOT NULL,
            skill_level cloud.skilllevel NOT NULL,
            PRIMARY KEY (operator_id, machine_type)
        )
    """)

    op.execute("""
        CREATE TABLE cloud.user (
            user_id VARCHAR(36) PRIMARY KEY,
            role cloud.userrole NOT NULL,
            linked_operator_id VARCHAR(36) REFERENCES cloud.operator(operator_id) ON DELETE SET NULL,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(256) NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.shift (
            shift_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL REFERENCES cloud.machine(machine_id),
            operator_id VARCHAR(36) NOT NULL REFERENCES cloud.operator(operator_id),
            date TIMESTAMPTZ NOT NULL,
            scheduled_start TIMESTAMPTZ NOT NULL,
            scheduled_end TIMESTAMPTZ NOT NULL,
            weather_forecast cloud.weathercategory NOT NULL,
            weather_actual VARCHAR(50) NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.task (
            task_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL REFERENCES cloud.shift(shift_id),
            task_type cloud.tasktype NOT NULL,
            operator_skill_at_assignment cloud.skilllevel NOT NULL,
            target_quantity FLOAT NOT NULL,
            quantity_unit cloud.quantityunit NOT NULL,
            material_type VARCHAR(100) NOT NULL,
            completed_quantity FLOAT NOT NULL DEFAULT 0,
            status cloud.taskstatus NOT NULL DEFAULT 'assigned',
            scheduled_start TIMESTAMPTZ NOT NULL,
            scheduled_end TIMESTAMPTZ,
            reassigned_at TIMESTAMPTZ,
            actual_start TIMESTAMPTZ,
            actual_end TIMESTAMPTZ,
            paused_minutes FLOAT NOT NULL DEFAULT 0,
            blocked_minutes FLOAT NOT NULL DEFAULT 0,
            raw_predicted_time FLOAT,
            planning_eta FLOAT,
            revised_predicted_time FLOAT,
            revised_at TIMESTAMPTZ,
            model_version VARCHAR(50),
            aggregates_version VARCHAR(50),
            is_fallback BOOLEAN NOT NULL DEFAULT false
        )
    """)

    op.execute("""
        CREATE TABLE cloud.shift_summary (
            shift_id VARCHAR(36) PRIMARY KEY REFERENCES cloud.shift(shift_id) ON DELETE CASCADE,
            engine_hours FLOAT NOT NULL,
            idle_minutes FLOAT NOT NULL,
            idle_ratio FLOAT NOT NULL,
            cycle_count INTEGER NOT NULL,
            completed_quantity FLOAT NOT NULL,
            fuel_used FLOAT NOT NULL,
            cycle_rate FLOAT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.behavior_baseline (
            baseline_id VARCHAR(36) PRIMARY KEY,
            scope VARCHAR(20) NOT NULL,
            scope_id VARCHAR(36),
            machine_type VARCHAR(50) NOT NULL,
            metric VARCHAR(50) NOT NULL,
            median FLOAT NOT NULL,
            mad FLOAT NOT NULL,
            sample_count INTEGER NOT NULL,
            version VARCHAR(50) NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.eta_aggregate (
            aggregate_id VARCHAR(36) PRIMARY KEY,
            scope VARCHAR(20) NOT NULL,
            scope_id VARCHAR(36),
            task_type VARCHAR(50) NOT NULL,
            avg_minutes_per_unit FLOAT NOT NULL,
            avg_idle_ratio FLOAT NOT NULL,
            avg_cycle_rate FLOAT NOT NULL,
            version VARCHAR(50) NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.injected_anomaly (
            anomaly_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL REFERENCES cloud.shift(shift_id),
            injected_anomaly_type VARCHAR(50) NOT NULL,
            event_start TIMESTAMPTZ NOT NULL,
            event_end TIMESTAMPTZ NOT NULL,
            notes TEXT
        )
    """)

    op.execute("""
        CREATE TABLE cloud.sync_conflict (
            conflict_id VARCHAR(36) PRIMARY KEY,
            task_id VARCHAR(36) NOT NULL REFERENCES cloud.task(task_id),
            conflict_type VARCHAR(50) NOT NULL,
            edge_actual_start TIMESTAMPTZ NOT NULL,
            cloud_reassigned_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            resolved BOOLEAN NOT NULL DEFAULT false
        )
    """)

    op.execute("""
        CREATE TABLE cloud.training_module (
            module_id VARCHAR(36) PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            format VARCHAR(50) NOT NULL,
            duration_min INTEGER NOT NULL,
            content_path VARCHAR(300)
        )
    """)

    op.execute("""
        CREATE TABLE cloud.quiz_result (
            result_id VARCHAR(36) PRIMARY KEY,
            operator_id VARCHAR(36) NOT NULL REFERENCES cloud.operator(operator_id),
            module_id VARCHAR(36) NOT NULL REFERENCES cloud.training_module(module_id),
            score FLOAT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.anomaly_training_map (
            map_id VARCHAR(36) PRIMARY KEY,
            source_type VARCHAR(20) NOT NULL,
            type_value VARCHAR(50) NOT NULL,
            recommended_module_id VARCHAR(36) NOT NULL REFERENCES cloud.training_module(module_id)
        )
    """)

    op.execute("""
        CREATE TABLE cloud.offline_credential (
            credential_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL REFERENCES cloud.shift(shift_id) ON DELETE CASCADE,
            operator_id VARCHAR(36) NOT NULL REFERENCES cloud.operator(operator_id),
            machine_id VARCHAR(36) NOT NULL REFERENCES cloud.machine(machine_id),
            pin_verifier VARCHAR(256) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            signature TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.audit_log (
            log_id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES cloud.user(user_id),
            action VARCHAR(100) NOT NULL,
            target_entity VARCHAR(100),
            target_id VARCHAR(36),
            timestamp TIMESTAMPTZ NOT NULL,
            tier cloud.tier NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE cloud.cloud_outbox (
            message_id VARCHAR(36) PRIMARY KEY,
            idempotency_key VARCHAR(100) NOT NULL UNIQUE,
            message_type VARCHAR(50) NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            delivered_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error VARCHAR(500)
        )
    """)

    # Edge tables
    op.execute("""
        CREATE TABLE edge.telemetry (
            telemetry_id VARCHAR(36) PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            shift_id VARCHAR(36) NOT NULL,
            engine_running BOOLEAN NOT NULL,
            engine_rpm FLOAT NOT NULL,
            engine_hours FLOAT NOT NULL,
            fuel_used FLOAT NOT NULL,
            hydraulic_active BOOLEAN NOT NULL,
            machine_speed FLOAT NOT NULL,
            load_cycles INTEGER NOT NULL,
            payload_pct FLOAT NOT NULL,
            cycle_payload_pct FLOAT,
            seatbelt_status VARCHAR(20) NOT NULL,
            seat_occupied BOOLEAN NOT NULL,
            park_brake BOOLEAN NOT NULL,
            gear_state VARCHAR(20) NOT NULL,
            proximity_distance FLOAT,
            ambient_temp FLOAT NOT NULL,
            visibility FLOAT NOT NULL,
            tilt_angle FLOAT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_telemetry_timestamp ON edge.telemetry (timestamp)")
    op.execute("CREATE INDEX ix_telemetry_machine_id ON edge.telemetry (machine_id)")
    op.execute("CREATE INDEX ix_telemetry_shift_id ON edge.telemetry (shift_id)")

    op.execute("""
        CREATE TABLE edge.incident (
            incident_id VARCHAR(36) PRIMARY KEY,
            shift_id VARCHAR(36) NOT NULL,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            event_start TIMESTAMPTZ NOT NULL,
            event_end TIMESTAMPTZ,
            incident_type edge.incidenttype NOT NULL,
            peak_severity edge.severity NOT NULL,
            escalation_reason edge.escalationreason,
            status edge.incidentstatus NOT NULL DEFAULT 'open',
            source VARCHAR(20) NOT NULL DEFAULT 'engine',
            description TEXT,
            acknowledged_at TIMESTAMPTZ,
            acknowledged_by VARCHAR(36)
        )
    """)
    op.execute("CREATE INDEX ix_incident_shift_id ON edge.incident (shift_id)")

    op.execute("""
        CREATE TABLE edge.behavior_event (
            event_id VARCHAR(36) PRIMARY KEY,
            machine_id VARCHAR(36) NOT NULL,
            operator_id VARCHAR(36) NOT NULL,
            task_id VARCHAR(36),
            shift_id VARCHAR(36) NOT NULL,
            event_type edge.behavioreventtype NOT NULL,
            start TIMESTAMPTZ NOT NULL,
            "end" TIMESTAMPTZ NOT NULL,
            magnitude FLOAT NOT NULL,
            baseline_value FLOAT
        )
    """)
    op.execute("CREATE INDEX ix_behavior_event_machine_id ON edge.behavior_event (machine_id)")
    op.execute("CREATE INDEX ix_behavior_event_shift_id ON edge.behavior_event (shift_id)")

    op.execute("""
        CREATE TABLE edge.recommendation (
            recommendation_id VARCHAR(36) PRIMARY KEY,
            operator_id VARCHAR(36) NOT NULL,
            shift_id VARCHAR(36) NOT NULL,
            module_id VARCHAR(36) NOT NULL,
            source_type VARCHAR(20) NOT NULL,
            source_id VARCHAR(36) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE edge.edge_outbox (
            message_id VARCHAR(36) PRIMARY KEY,
            idempotency_key VARCHAR(100) NOT NULL UNIQUE,
            message_type VARCHAR(50) NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            delivered_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error VARCHAR(500)
        )
    """)


def downgrade() -> None:
    for table, schema in [
        ("edge_outbox", "edge"),
        ("recommendation", "edge"),
        ("behavior_event", "edge"),
        ("incident", "edge"),
        ("telemetry", "edge"),
        ("cloud_outbox", "cloud"),
        ("audit_log", "cloud"),
        ("offline_credential", "cloud"),
        ("anomaly_training_map", "cloud"),
        ("quiz_result", "cloud"),
        ("training_module", "cloud"),
        ("sync_conflict", "cloud"),
        ("injected_anomaly", "cloud"),
        ("eta_aggregate", "cloud"),
        ("behavior_baseline", "cloud"),
        ("shift_summary", "cloud"),
        ("task", "cloud"),
        ("shift", "cloud"),
        ("user", "cloud"),
        ("operator_qualification", "cloud"),
        ("operator", "cloud"),
        ("machine", "cloud"),
    ]:
        op.execute(f'DROP TABLE IF EXISTS {schema}."{table}" CASCADE')

    for name, schema in [
        ("behavioreventtype", "edge"),
        ("escalationreason", "edge"),
        ("incidentstatus", "edge"),
        ("severity", "edge"),
        ("incidenttype", "edge"),
        ("tier", "cloud"),
        ("userrole", "cloud"),
        ("weathercategory", "cloud"),
        ("taskstatus", "cloud"),
        ("quantityunit", "cloud"),
        ("tasktype", "cloud"),
        ("skilllevel", "cloud"),
        ("machinestatus", "cloud"),
        ("machinetype", "cloud"),
    ]:
        op.execute(f"DROP TYPE IF EXISTS {schema}.{name}")

    op.execute("DROP SCHEMA IF EXISTS edge CASCADE")
    op.execute("DROP SCHEMA IF EXISTS cloud CASCADE")
