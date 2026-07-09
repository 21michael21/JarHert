"""Add monitor jobs.

Revision ID: 0003_monitor_jobs
Revises: 0002_trace_ids
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_monitor_jobs"
down_revision = "0002_trace_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _table_exists("monitor_jobs"):
        op.create_table(
            "monitor_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("source_type", sa.String(length=80), nullable=False),
            sa.Column("source_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("condition_text", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_state_hash", sa.String(length=64), nullable=True),
            sa.Column("last_payload", sa.JSON(), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_monitor_jobs_enabled_created", "monitor_jobs", ["enabled", "created_at"])
    _create_index_if_missing("ix_monitor_jobs_user_enabled", "monitor_jobs", ["user_id", "enabled"])

    if not _table_exists("monitor_runs"):
        op.create_table(
            "monitor_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("monitor_job_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["monitor_job_id"], ["monitor_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_monitor_runs_job_created", "monitor_runs", ["monitor_job_id", "created_at"])


def downgrade() -> None:
    _drop_index_if_exists("ix_monitor_runs_job_created", "monitor_runs")
    if _table_exists("monitor_runs"):
        op.drop_table("monitor_runs")
    _drop_index_if_exists("ix_monitor_jobs_user_enabled", "monitor_jobs")
    _drop_index_if_exists("ix_monitor_jobs_enabled_created", "monitor_jobs")
    if _table_exists("monitor_jobs"):
        op.drop_table("monitor_jobs")


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
