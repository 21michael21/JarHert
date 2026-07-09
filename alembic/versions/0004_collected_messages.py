"""Add collected Telegram messages.

Revision ID: 0004_collected_messages
Revises: 0003_monitor_jobs
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_collected_messages"
down_revision = "0003_monitor_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("telegram_message_id", sa.Integer(), nullable=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_title", sa.String(length=250), nullable=True),
            sa.Column("sender_id", sa.BigInteger(), nullable=True),
            sa.Column("sender_name", sa.String(length=250), nullable=True),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_processed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("chat_id", "telegram_message_id", name="uq_messages_chat_message"),
        )
    _create_index_if_missing("ix_messages_processed_timestamp", "messages", ["is_processed", "timestamp"])
    _create_index_if_missing("ix_messages_chat_timestamp", "messages", ["chat_id", "timestamp"])


def downgrade() -> None:
    _drop_index_if_exists("ix_messages_chat_timestamp", "messages")
    _drop_index_if_exists("ix_messages_processed_timestamp", "messages")
    if _table_exists("messages"):
        op.drop_table("messages")


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return index_name in {index["name"] for index in _inspector().get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
