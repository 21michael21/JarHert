"""Add recurring reminders.

Revision ID: 0017_recurring_reminders
Revises: 0016_preference_reasons
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_recurring_reminders"
down_revision = "0016_preference_reasons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _column_exists("reminders", "recurrence"):
        op.add_column("reminders", sa.Column("recurrence", sa.String(length=30), nullable=True))


def downgrade() -> None:
    if _column_exists("reminders", "recurrence"):
        op.drop_column("reminders", "recurrence")


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {item["name"] for item in inspector.get_columns(table_name)}
