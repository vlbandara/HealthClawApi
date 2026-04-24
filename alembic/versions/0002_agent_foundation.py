"""agent foundation hardening

Revision ID: 0002_agent_foundation
Revises: 0001_initial
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_agent_foundation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("proactive_max_per_day", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "users",
        sa.Column(
            "proactive_cooldown_minutes", sa.Integer(), nullable=False, server_default="180"
        ),
    )
    op.add_column(
        "identities",
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
    )
    op.add_column("messages", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.add_column(
        "memories", sa.Column("semantic_text", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "memories", sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("memories", sa.Column("refresh_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("memory_revisions", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.add_column(
        "reminders", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("reminders", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("reminders", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proactive_events", sa.Column("channel", sa.String(length=32), nullable=True))
    op.add_column("proactive_events", sa.Column("trace_id", sa.String(length=64), nullable=True))

    op.create_table(
        "policy_proposals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("proposed_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "inbound_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "user_message_id", sa.String(length=64), sa.ForeignKey("messages.id"), nullable=True
        ),
        sa.Column(
            "assistant_message_id",
            sa.String(length=64),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel", "idempotency_key", name="uq_inbound_channel_idempotency"),
    )
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(length=64),
            sa.ForeignKey("conversation_threads.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_checkpoints")
    op.drop_table("inbound_events")
    op.drop_table("policy_proposals")
    for table, column in [
        ("proactive_events", "trace_id"),
        ("proactive_events", "channel"),
        ("reminders", "sent_at"),
        ("reminders", "last_error"),
        ("reminders", "attempts"),
        ("memory_revisions", "trace_id"),
        ("memories", "metadata"),
        ("memories", "expires_at"),
        ("memories", "refresh_after"),
        ("memories", "last_confirmed_at"),
        ("memories", "semantic_text"),
        ("messages", "trace_id"),
        ("identities", "status"),
        ("users", "proactive_cooldown_minutes"),
        ("users", "proactive_max_per_day"),
    ]:
        op.drop_column(table, column)
