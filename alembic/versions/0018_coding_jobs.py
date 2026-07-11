"""Add remote coding jobs.

Revision ID: 0018_coding_jobs
Revises: 0017_recurring_reminders
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_coding_jobs"
down_revision = "0017_recurring_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coding_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("repository_url", sa.Text()),
        sa.Column("source_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(length=180)),
        sa.Column("worker_id", sa.String(length=100)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("result_text", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_coding_jobs_user_idempotency"),
    )
    op.create_index("ix_coding_jobs_status_created", "coding_jobs", ["status", "created_at"])
    op.create_index("ix_coding_jobs_status_lease", "coding_jobs", ["status", "lease_until"])


def downgrade() -> None:
    op.drop_index("ix_coding_jobs_status_lease", table_name="coding_jobs")
    op.drop_index("ix_coding_jobs_status_created", table_name="coding_jobs")
    op.drop_table("coding_jobs")
