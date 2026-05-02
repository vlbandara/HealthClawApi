"""WS6: motives, metric_logs, action_audit, thought intent fields

Revision ID: 0012_ws6_motives_metrics_audit
Revises: 0011_sensing_layer
Create Date: 2026-05-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_ws6_motives_metrics_audit"
down_revision = "0011_sensing_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── motives table ─────────────────────────────────────────────────────────
    op.create_table(
        "motives",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="seeded"),
        sa.Column("decay_half_life_days", sa.Integer(), nullable=False, server_default="21"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reinforced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_decayed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", "name", name="uq_motive_user_name"),
    )
    op.create_index("ix_motives_user_id", "motives", ["user_id"])

    # ── metric_logs table ────────────────────────────────────────────────────
    op.create_table(
        "metric_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "source_message_id",
            sa.String(64),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_metric_logs_user_id", "metric_logs", ["user_id"])
    op.create_index(
        "ix_metric_logs_user_metric_observed",
        "metric_logs",
        ["user_id", "metric", "observed_at"],
    )

    # ── action_audit table ───────────────────────────────────────────────────
    op.create_table(
        "action_audit",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "message_id",
            sa.String(64),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="executed"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("safety_category", sa.String(32), nullable=False, server_default="normal"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_action_audit_user_id", "action_audit", ["user_id"])

    # ── thoughts: add intent fields ──────────────────────────────────────────
    op.add_column("thoughts", sa.Column("intent_kind", sa.String(32), nullable=True))
    op.add_column("thoughts", sa.Column("intent_motive", sa.String(64), nullable=True))
    op.add_column("thoughts", sa.Column("discarded_reason", sa.String(128), nullable=True))
    op.add_column(
        "thoughts",
        sa.Column("deferred_to", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thoughts", "deferred_to")
    op.drop_column("thoughts", "discarded_reason")
    op.drop_column("thoughts", "intent_motive")
    op.drop_column("thoughts", "intent_kind")
    op.drop_index("ix_action_audit_user_id", "action_audit")
    op.drop_table("action_audit")
    op.drop_index("ix_metric_logs_user_metric_observed", "metric_logs")
    op.drop_index("ix_metric_logs_user_id", "metric_logs")
    op.drop_table("metric_logs")
    op.drop_index("ix_motives_user_id", "motives")
    op.drop_table("motives")
