"""continuity runtime

Revision ID: 0003_continuity_runtime
Revises: 0002_agent_foundation
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_continuity_runtime"
down_revision = "0002_agent_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in [
        sa.Column("onboarding_status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column(
            "consent_version", sa.String(length=32), nullable=False, server_default="wellness-v1"
        ),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="en"),
        sa.Column(
            "notification_channel", sa.String(length=32), nullable=False, server_default="telegram"
        ),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proactive_paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "monthly_llm_token_budget",
            sa.Integer(),
            nullable=False,
            server_default="500000",
        ),
        sa.Column("monthly_llm_tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("monthly_llm_cost_cents_used", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("users", column)

    for column in [
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("open_loop_count", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("conversation_threads", column)

    for column in [
        sa.Column("layer", sa.String(length=32), nullable=False, server_default="durable"),
        sa.Column("freshness_score", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=24),
            nullable=False,
            server_default="user_visible",
        ),
        sa.Column("user_editable", sa.Boolean(), nullable=False, server_default=sa.true()),
    ]:
        op.add_column("memories", column)

    op.create_table(
        "user_soul_preferences",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tone_preferences", sa.JSON(), nullable=False),
        sa.Column("response_preferences", sa.JSON(), nullable=False),
        sa.Column("blocked_policy_keys", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_soul_preferences_user"),
    )
    op.create_table(
        "open_loops",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "thread_id",
            sa.String(length=64),
            sa.ForeignKey("conversation_threads.id"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id", sa.String(length=64), sa.ForeignKey("messages.id"), nullable=True
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("due_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "heartbeat_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "open_loop_id",
            sa.String(length=64),
            sa.ForeignKey("open_loops.id"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_heartbeat_idempotency"),
    )
    op.create_table(
        "heartbeat_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("heartbeat_jobs.id"),
            nullable=True,
        ),
        sa.Column(
            "open_loop_id",
            sa.String(length=64),
            sa.ForeignKey("open_loops.id"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_engagement_states",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_user_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_assistant_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_count", sa.Integer(), nullable=False),
        sa.Column("voice_note_count", sa.Integer(), nullable=False),
        sa.Column("lapse_count", sa.Integer(), nullable=False),
        sa.Column("weekly_reflection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_engagement_user"),
    )
    op.create_table(
        "user_quotas",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("cost_cents_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "period_key", name="uq_user_quota_period"),
    )


def downgrade() -> None:
    for table in [
        "user_quotas",
        "user_engagement_states",
        "heartbeat_events",
        "heartbeat_jobs",
        "open_loops",
        "user_soul_preferences",
    ]:
        op.drop_table(table)
    for table, column in [
        ("memories", "user_editable"),
        ("memories", "visibility"),
        ("memories", "last_accessed_at"),
        ("memories", "freshness_score"),
        ("memories", "layer"),
        ("conversation_threads", "open_loop_count"),
        ("conversation_threads", "summary"),
        ("conversation_threads", "is_primary"),
        ("users", "monthly_llm_cost_cents_used"),
        ("users", "monthly_llm_tokens_used"),
        ("users", "monthly_llm_token_budget"),
        ("users", "proactive_paused_until"),
        ("users", "last_active_at"),
        ("users", "notification_channel"),
        ("users", "locale"),
        ("users", "consent_version"),
        ("users", "onboarding_status"),
    ]:
        op.drop_column(table, column)
