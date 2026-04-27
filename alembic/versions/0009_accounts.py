"""accounts and magic-link auth for multi-tenant onboarding

Revision ID: 0009_accounts
Revises: 0008_relationship_telemetry
Create Date: 2026-04-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_accounts"
down_revision = "0008_relationship_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("bot_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("bot_username", sa.String(length=64), nullable=True),
        sa.Column("bot_telegram_id", sa.String(length=64), nullable=True),
        sa.Column("webhook_secret", sa.String(length=96), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column(
            "monthly_message_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("monthly_message_period_start", sa.String(length=7), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("email", name="uq_accounts_email"),
    )
    op.create_table(
        "auth_magic_links",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "account_id", sa.String(length=64), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_auth_magic_links_account_id", "auth_magic_links", ["account_id"]
    )
    op.create_index(
        "ix_auth_magic_links_token_hash", "auth_magic_links", ["token_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_magic_links_token_hash", table_name="auth_magic_links")
    op.drop_index("ix_auth_magic_links_account_id", table_name="auth_magic_links")
    op.drop_table("auth_magic_links")
    op.drop_table("accounts")
