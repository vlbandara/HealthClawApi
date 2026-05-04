"""WS7: naturalness pass — timezone_confidence on users, engagement fields on open_loops

Revision ID: 0013_ws7_naturalness_pass
Revises: 0012_ws6_motives_metrics_audit
Create Date: 2026-05-03
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_ws7_naturalness_pass"
down_revision = "0012_ws6_motives_metrics_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users: timezone confidence
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("timezone_confidence", sa.Float(), nullable=False, server_default="0.0")
        )

    # open_loops: topic engagement tracking
    with op.batch_alter_table("open_loops") as batch_op:
        batch_op.add_column(
            sa.Column("surface_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("last_surfaced_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="12")
        )
        batch_op.add_column(
            sa.Column("max_surfaces", sa.Integer(), nullable=False, server_default="2")
        )
        batch_op.add_column(
            sa.Column("engaged_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("engagement_score", sa.Float(), nullable=False, server_default="0.0")
        )
        batch_op.add_column(
            sa.Column("disengage_count", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("open_loops") as batch_op:
        batch_op.drop_column("disengage_count")
        batch_op.drop_column("engagement_score")
        batch_op.drop_column("engaged_at")
        batch_op.drop_column("max_surfaces")
        batch_op.drop_column("cooldown_hours")
        batch_op.drop_column("cooldown_until")
        batch_op.drop_column("last_surfaced_at")
        batch_op.drop_column("surface_count")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("timezone_confidence")
