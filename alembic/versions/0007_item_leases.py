"""Add item-level leases for actions and delivery.

Revision ID: 0007_item_leases
Revises: 0006_automation_runtime
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_item_leases"
down_revision = "0006_automation_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("agent_actions", "delivery_outbox"):
        _add_column(table_name, sa.Column("worker_id", sa.String(length=100)))
        _add_column(table_name, sa.Column("lease_until", sa.DateTime(timezone=True)))
        _add_column(table_name, sa.Column("claimed_at", sa.DateTime(timezone=True)))
        _add_column(table_name, sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    _create_index("agent_actions", "ix_agent_actions_status_lease", ["status", "lease_until"])
    _create_index("delivery_outbox", "ix_delivery_outbox_status_lease", ["status", "lease_until"])


def downgrade() -> None:
    for table_name, index_name in (
        ("agent_actions", "ix_agent_actions_status_lease"),
        ("delivery_outbox", "ix_delivery_outbox_status_lease"),
    ):
        inspector = sa.inspect(op.get_bind())
        if index_name in {item["name"] for item in inspector.get_indexes(table_name)}:
            op.drop_index(index_name, table_name=table_name)
        for column in ("heartbeat_at", "claimed_at", "lease_until", "worker_id"):
            if column in {item["name"] for item in inspector.get_columns(table_name)}:
                op.drop_column(table_name, column)


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def _create_index(table_name: str, index_name: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns)
