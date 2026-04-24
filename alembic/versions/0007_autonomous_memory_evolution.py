"""autonomous memory evolution audit tables

Revision ID: 0007_autonomous_memory_evolution
Revises: 0006_user_memory_documents
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_autonomous_memory_evolution"
down_revision = "0006_user_memory_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("has_embedding", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "dream_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="started"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dream_runs_user_id", "dream_runs", ["user_id"])

    op.create_table(
        "dream_changes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("dream_runs.id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_key", sa.String(160), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("protected_policy_check", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_dream_changes_run_id", "dream_changes", ["run_id"])
    op.create_index("ix_dream_changes_user_id", "dream_changes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_dream_changes_user_id", table_name="dream_changes")
    op.drop_index("ix_dream_changes_run_id", table_name="dream_changes")
    op.drop_table("dream_changes")
    op.drop_index("ix_dream_runs_user_id", table_name="dream_runs")
    op.drop_table("dream_runs")
    op.drop_column("memories", "has_embedding")
