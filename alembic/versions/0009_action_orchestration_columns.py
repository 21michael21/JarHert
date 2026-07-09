"""Add missing action orchestration columns.

Revision ID: 0009_action_orchestration_columns
Revises: 0008_update_idempotency
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_action_orchestration_columns"
down_revision = "0008_update_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column("depends_on_action_id", sa.Integer())
    _add_column("compensation_for_action_id", sa.Integer())
    _add_column(
        "compensation_status",
        sa.String(length=30),
        nullable=False,
        server_default="none",
    )


def downgrade() -> None:
    for column_name in (
        "compensation_status",
        "compensation_for_action_id",
        "depends_on_action_id",
    ):
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agent_actions")}
        if column_name in columns:
            op.drop_column("agent_actions", column_name)


def _add_column(name: str, column_type, **kwargs) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agent_actions")}
    if name not in columns:
        op.add_column("agent_actions", sa.Column(name, column_type, **kwargs))
