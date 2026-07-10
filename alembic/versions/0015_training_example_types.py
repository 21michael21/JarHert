"""Separate consented training examples by response type.

Revision ID: 0015_training_example_types
Revises: 0014_training_feedback
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_training_example_types"
down_revision = "0014_training_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_examples", sa.Column("rejected_assistant_text", sa.Text(), nullable=True))
    op.add_column(
        "training_examples",
        sa.Column("example_type", sa.String(length=30), nullable=False, server_default="short_answer"),
    )


def downgrade() -> None:
    op.drop_column("training_examples", "example_type")
    op.drop_column("training_examples", "rejected_assistant_text")
