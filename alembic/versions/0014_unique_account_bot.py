"""enforce unique Telegram bot ownership

Revision ID: 0014_unique_account_bot
Revises: 0013_ws7_naturalness_pass
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op

revision = "0014_unique_account_bot"
down_revision = "0013_ws7_naturalness_pass"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_accounts_bot_telegram_id",
        "accounts",
        ["bot_telegram_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_accounts_bot_telegram_id", "accounts", type_="unique")
