"""Add durable automation worker leases.

Revision ID: 0006_automation_runtime
Revises: 0005_agent_action_result_meta
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_automation_runtime"
down_revision = "0005_agent_action_result_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "automation_worker_leases" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "automation_worker_leases",
            sa.Column("worker_name", sa.String(length=100), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
            sa.Column("owner_id", sa.String(length=100)),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_until", sa.DateTime(timezone=True)),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
            sa.Column("next_run_at", sa.DateTime(timezone=True)),
            sa.Column("last_started_at", sa.DateTime(timezone=True)),
            sa.Column("last_completed_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("worker_name"),
        )
    _create_index("ix_automation_worker_status_next_run", ["status", "next_run_at"])
    _create_index("ix_automation_worker_lease_until", ["lease_until"])


def downgrade() -> None:
    if "automation_worker_leases" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("automation_worker_leases")


def _create_index(name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes("automation_worker_leases")}
    if name not in existing:
        op.create_index(name, "automation_worker_leases", columns)
