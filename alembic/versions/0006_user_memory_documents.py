"""add user memory documents

Revision ID: 0006_user_memory_documents
Revises: 0005_rituals_heartbeat
Create Date: 2026-04-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_user_memory_documents"
down_revision = "0005_rituals_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", name="uq_user_memory_document_kind"),
    )


def downgrade() -> None:
    op.drop_table("user_memory_documents")
