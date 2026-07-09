"""Add monitor run message.

Revision ID: 0013_monitor_run_message
Revises: 0012_contact_book
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_monitor_run_message"
down_revision = "0012_contact_book"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if _table_exists("monitor_runs") and not _column_exists("monitor_runs", "message"):
        op.add_column("monitor_runs", sa.Column("message", sa.Text(), nullable=True))


def downgrade() -> None:
    if _table_exists("monitor_runs") and _column_exists("monitor_runs", "message"):
        op.drop_column("monitor_runs", "message")


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}
