"""Add explicitly consented training examples.

Revision ID: 0014_training_feedback
Revises: 0013_monitor_run_message
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_training_feedback"
down_revision = "0013_monitor_run_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_examples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "conversation_turn_id",
            sa.Integer(),
            sa.ForeignKey("conversation_turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("assistant_text", sa.Text(), nullable=True),
        sa.Column("feedback_kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="approved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "conversation_turn_id", name="uq_training_examples_user_turn"),
    )
    op.create_index(
        "ix_training_examples_user_status_created",
        "training_examples",
        ["user_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_training_examples_user_status_created", table_name="training_examples")
    op.drop_table("training_examples")
