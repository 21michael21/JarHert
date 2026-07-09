"""Add agent action result metadata.

Revision ID: 0005_agent_action_result_meta
Revises: 0004_collected_messages
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_agent_action_result_meta"
down_revision = "0004_collected_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column_if_missing(
        "agent_actions",
        sa.Column("result_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    _drop_column_if_exists("agent_actions", "result_meta")


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _column_exists(table_name, column.name):
        return
    op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)
