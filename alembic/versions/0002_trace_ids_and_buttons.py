"""Add trace ids and delivery buttons.

Revision ID: 0002_trace_ids
Revises: 0001_initial
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_trace_ids"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column_if_missing("agent_jobs", sa.Column("trace_id", sa.String(length=40), nullable=True))
    _create_index_if_missing("ix_agent_jobs_trace", "agent_jobs", ["trace_id"])

    _add_column_if_missing("agent_actions", sa.Column("trace_id", sa.String(length=40), nullable=True))
    _create_index_if_missing("ix_agent_actions_trace", "agent_actions", ["trace_id"])

    _add_column_if_missing("delivery_outbox", sa.Column("trace_id", sa.String(length=40), nullable=True))
    _add_column_if_missing("delivery_outbox", sa.Column("buttons", sa.JSON(), nullable=True))
    _create_index_if_missing("ix_delivery_outbox_trace", "delivery_outbox", ["trace_id"])

    _add_column_if_missing("events", sa.Column("trace_id", sa.String(length=40), nullable=True))
    _create_index_if_missing("ix_events_trace", "events", ["trace_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_events_trace", "events")
    _drop_column_if_exists("events", "trace_id")

    _drop_index_if_exists("ix_delivery_outbox_trace", "delivery_outbox")
    _drop_column_if_exists("delivery_outbox", "buttons")
    _drop_column_if_exists("delivery_outbox", "trace_id")

    _drop_index_if_exists("ix_agent_actions_trace", "agent_actions")
    _drop_column_if_exists("agent_actions", "trace_id")

    _drop_index_if_exists("ix_agent_jobs_trace", "agent_jobs")
    _drop_column_if_exists("agent_jobs", "trace_id")


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return index_name in {index["name"] for index in _inspector().get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _column_exists(table_name, column.name):
        return
    op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
