"""rituals table, heartbeat_md on users, decision fields on heartbeat_events

Revision ID: 0005_rituals_heartbeat
Revises: 0004_memory_substrate
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_rituals_heartbeat"
down_revision = "0004_memory_substrate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-user standing intents doc (nanobot's HEARTBEAT.md as a column)
    op.add_column("users", sa.Column("heartbeat_md", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "users",
        sa.Column("heartbeat_md_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Rituals: standing scheduled intents (morning check-in, evening reflection, etc.)
    op.create_table(
        "rituals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("schedule_cron", sa.String(64), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("streak_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("streak_last_date", sa.String(10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_rituals_user_id", "rituals", ["user_id"])

    # Extend heartbeat_events with decision audit fields
    op.add_column(
        "heartbeat_events",
        sa.Column("decision_input", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "heartbeat_events",
        sa.Column("decision_model", sa.String(64), nullable=True),
    )
    op.add_column(
        "heartbeat_events",
        sa.Column("skip_reason", sa.String(128), nullable=True),
    )

    # ritual_id FK on heartbeat_jobs (nullable — existing jobs have none)
    op.add_column(
        "heartbeat_jobs",
        sa.Column("ritual_id", sa.String(64), sa.ForeignKey("rituals.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("heartbeat_jobs", "ritual_id")
    op.drop_column("heartbeat_events", "skip_reason")
    op.drop_column("heartbeat_events", "decision_model")
    op.drop_column("heartbeat_events", "decision_input")
    op.drop_index("ix_rituals_user_id", table_name="rituals")
    op.drop_table("rituals")
    op.drop_column("users", "heartbeat_md_updated_at")
    op.drop_column("users", "heartbeat_md")
