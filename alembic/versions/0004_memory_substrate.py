"""memory substrate: pgvector on memories, user_memory_cursors, engagement trust fields

Revision ID: 0004_memory_substrate
Revises: 0003_continuity_runtime
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_memory_substrate"
down_revision = "0003_continuity_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add embedding column to memories
    op.add_column("memories", sa.Column("embedding", sa.Text(), nullable=True))
    # Note: We store as Text and cast at query time via raw SQL until pgvector
    # SQLAlchemy type is available; the HybridRetriever handles the ANN query directly.
    # After the extension is live and pgvector python package is installed, we use
    # raw DDL to set the real vector type.
    op.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_embedding_user "
        "ON memories USING ivfflat (embedding_vec vector_cosine_ops) WITH (lists = 100)"
    )

    # user_memory_cursors: one row per user, tracks consolidation + dream offsets
    op.create_table(
        "user_memory_cursors",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "consolidation_cursor_msg_id",
            sa.String(64),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("consolidation_cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dream_cursor_msg_id",
            sa.String(64),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("dream_cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "embedding_cursor_memory_id",
            sa.String(64),
            sa.ForeignKey("memories.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("user_id", name="uq_memory_cursor_user"),
    )

    # Extend user_engagement_states for continuity bridges
    op.add_column(
        "user_engagement_states",
        sa.Column("trust_level", sa.Float(), nullable=False, server_default="0.3"),
    )
    op.add_column(
        "user_engagement_states",
        sa.Column("open_loop_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user_engagement_states", "open_loop_count")
    op.drop_column("user_engagement_states", "trust_level")
    op.drop_table("user_memory_cursors")
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_user")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding_vec")
    op.drop_column("memories", "embedding")
