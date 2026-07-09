"""Add provider quality and budget policy storage.

Revision ID: 0010_provider_policy
Revises: 0009_action_orchestration_columns
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_provider_policy"
down_revision = "0009_action_orchestration_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column("provider_health", sa.Column("quality_score", sa.Integer(), nullable=False, server_default="100"))
    _add_column("provider_health", sa.Column("quality_sample_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "provider_budget_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.String(length=10), nullable=False, unique=True),
        sa.Column("estimated_cost_micro_usd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "provider_budget_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("estimated_cost_micro_usd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_provider_budget_entries_day_provider", "provider_budget_entries", ["day", "provider_name"])


def downgrade() -> None:
    op.drop_index("ix_provider_budget_entries_day_provider", table_name="provider_budget_entries")
    op.drop_table("provider_budget_entries")
    op.drop_table("provider_budget_daily")
    for column_name in ("quality_sample_count", "quality_score"):
        if column_name in {item["name"] for item in sa.inspect(op.get_bind()).get_columns("provider_health")}:
            op.drop_column("provider_health", column_name)


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)
