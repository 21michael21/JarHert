"""Store deterministic explanations for preference pairs.

Revision ID: 0016_preference_reasons
Revises: 0015_training_example_types
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_preference_reasons"
down_revision = "0015_training_example_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_examples", sa.Column("preference_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_examples", "preference_reason")
