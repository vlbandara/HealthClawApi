"""relationship telemetry fields on user engagement states

Revision ID: 0008_relationship_telemetry
Revises: 0007_autonomous_memory_evolution
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_relationship_telemetry"
down_revision = "0007_autonomous_memory_evolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_engagement_states",
        sa.Column("sentiment_ema", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "user_engagement_states",
        sa.Column("voice_text_ratio", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "user_engagement_states",
        sa.Column("reply_latency_seconds_ema", sa.Float(), nullable=True),
    )
    op.add_column(
        "user_engagement_states",
        sa.Column("last_meaningful_exchange_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_engagement_states", "last_meaningful_exchange_at")
    op.drop_column("user_engagement_states", "reply_latency_seconds_ema")
    op.drop_column("user_engagement_states", "voice_text_ratio")
    op.drop_column("user_engagement_states", "sentiment_ema")
