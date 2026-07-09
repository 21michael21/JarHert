"""Add personal knowledge notes.

Revision ID: 0011_personal_knowledge
Revises: 0010_provider_policy
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_personal_knowledge"
down_revision = "0010_provider_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False, server_default="note"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="telegram"),
        sa.Column("project", sa.String(length=120), nullable=True),
        sa.Column("contact", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notes_user_status_updated", "notes", ["user_id", "status", "updated_at"])
    op.create_index("ix_notes_user_type_updated", "notes", ["user_id", "type", "updated_at"])
    op.create_index("ix_notes_user_project", "notes", ["user_id", "project"])
    op.create_index("ix_notes_user_contact", "notes", ["user_id", "contact"])

    op.create_table(
        "note_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("note_id", sa.Integer(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("before_text", sa.Text(), nullable=True),
        sa.Column("after_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_note_history_note_created", "note_history", ["note_id", "created_at"])
    _copy_legacy_items()


def downgrade() -> None:
    op.drop_index("ix_note_history_note_created", table_name="note_history")
    op.drop_table("note_history")
    op.drop_index("ix_notes_user_contact", table_name="notes")
    op.drop_index("ix_notes_user_project", table_name="notes")
    op.drop_index("ix_notes_user_type_updated", table_name="notes")
    op.drop_index("ix_notes_user_status_updated", table_name="notes")
    op.drop_table("notes")


def _copy_legacy_items() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "memories" in tables:
        bind.execute(
            sa.text(
                """
                INSERT INTO notes (user_id, type, text, source, status, created_at, updated_at)
                SELECT user_id, 'memory', text, 'legacy_memory', 'active', created_at, created_at
                FROM memories
                """
            )
        )
    if "ideas" in tables:
        bind.execute(
            sa.text(
                """
                INSERT INTO notes (user_id, type, text, source, status, created_at, updated_at)
                SELECT user_id, 'idea', text, 'legacy_idea', 'active', created_at, created_at
                FROM ideas
                """
            )
        )
