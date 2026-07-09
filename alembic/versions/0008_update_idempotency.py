"""Add Telegram update idempotency across jobs and delivery.

Revision ID: 0008_update_idempotency
Revises: 0007_item_leases
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_update_idempotency"
down_revision = "0007_item_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column("agent_jobs", sa.Column("idempotency_key", sa.String(length=180)))
    _add_column("delivery_outbox", sa.Column("idempotency_key", sa.String(length=180)))
    _add_column("agent_actions", sa.Column("result_text", sa.Text()))
    _create_index(
        "agent_jobs",
        "ux_agent_jobs_user_idempotency",
        ["user_id", "idempotency_key"],
        unique=True,
    )
    _create_index(
        "delivery_outbox",
        "ux_delivery_outbox_user_idempotency",
        ["user_id", "idempotency_key"],
        unique=True,
    )
    if "inbound_updates" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "inbound_updates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("idempotency_key", sa.String(length=220), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="processing"),
            sa.Column("response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("trace_id", sa.String(length=40)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("user_id", "idempotency_key", name="uq_inbound_updates_user_idempotency"),
        )
    _create_index(
        "inbound_updates",
        "ix_inbound_updates_status_updated",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    if "inbound_updates" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("inbound_updates")
    for table_name, index_name in (
        ("delivery_outbox", "ux_delivery_outbox_user_idempotency"),
        ("agent_jobs", "ux_agent_jobs_user_idempotency"),
    ):
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
    for table_name, column_name in (
        ("agent_actions", "result_text"),
        ("delivery_outbox", "idempotency_key"),
        ("agent_jobs", "idempotency_key"),
    ):
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
        if column_name in columns:
            op.drop_column(table_name, column_name)


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def _create_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=unique)
