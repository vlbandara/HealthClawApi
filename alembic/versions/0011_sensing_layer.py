"""WS5: afferent sensing layer — signals, user_locations, integration_credentials, thoughts

Revision ID: 0011_sensing_layer
Revises: 0010_soft_schemas
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_sensing_layer"
down_revision = "0010_soft_schemas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend User with location + chronotype
    op.add_column("users", sa.Column("home_lat", sa.Float(), nullable=True))
    op.add_column("users", sa.Column("home_lon", sa.Float(), nullable=True))
    op.add_column(
        "users",
        sa.Column("chronotype", sa.String(length=16), nullable=False, server_default="intermediate"),
    )

    op.create_table(
        "user_locations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False, server_default="home"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_user_locations_user_id", "user_locations", ["user_id"])

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_integration_credential_user_provider"
        ),
    )
    op.create_index("ix_integration_credentials_user_id", "integration_credentials", ["user_id"])

    op.create_table(
        "signals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_signals_user_id", "signals", ["user_id"])
    op.create_index("ix_signals_dedup_key", "signals", ["dedup_key"])
    op.create_index(
        "ix_signals_user_kind_observed",
        "signals",
        ["user_id", "kind", "observed_at"],
    )

    op.create_table(
        "thoughts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False, server_default="passive"),
        sa.Column("content_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("salience", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column(
            "salience_breakdown", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("signal_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("time_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "became_utterance", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "heartbeat_job_id",
            sa.String(length=64),
            sa.ForeignKey("heartbeat_jobs.id"),
            nullable=True,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_thoughts_user_id", "thoughts", ["user_id"])
    op.create_index("ix_thoughts_user_created", "thoughts", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("thoughts")
    op.drop_table("signals")
    op.drop_table("integration_credentials")
    op.drop_table("user_locations")
    op.drop_column("users", "chronotype")
    op.drop_column("users", "home_lon")
    op.drop_column("users", "home_lat")
