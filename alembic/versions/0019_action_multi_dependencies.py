"""Store every action dependency for real Planner DAG joins.

Revision ID: 0019_action_multi_dependencies
Revises: 0018_coding_jobs
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_action_multi_dependencies"
down_revision = "0018_coding_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("agent_actions")}
    if "depends_on_action_ids" not in columns:
        op.add_column(
            "agent_actions",
            sa.Column("depends_on_action_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
    actions = sa.table(
        "agent_actions",
        sa.column("id", sa.Integer()),
        sa.column("depends_on_action_id", sa.Integer()),
        sa.column("depends_on_action_ids", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(actions.c.id, actions.c.depends_on_action_id).where(actions.c.depends_on_action_id.is_not(None))
    ).all()
    for action_id, dependency_id in rows:
        bind.execute(
            actions.update()
            .where(actions.c.id == action_id)
            .values(depends_on_action_ids=[int(dependency_id)])
        )


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agent_actions")}
    if "depends_on_action_ids" in columns:
        op.drop_column("agent_actions", "depends_on_action_ids")
