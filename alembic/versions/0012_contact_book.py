"""Add contact book.

Revision ID: 0012_contact_book
Revises: 0011_personal_knowledge
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_contact_book"
down_revision = "0011_personal_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=180), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "normalized_name", name="uq_contacts_user_normalized_name"),
    )
    op.create_index("ix_contacts_user_name", "contacts", ["user_id", "normalized_name"])
    op.create_index("ix_contacts_tg_user", "contacts", ["tg_user_id"])
    op.create_table(
        "contact_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(length=160), nullable=False),
        sa.Column("normalized_alias", sa.String(length=180), nullable=False),
        sa.UniqueConstraint("user_id", "normalized_alias", name="uq_contact_aliases_user_alias"),
    )
    op.create_index("ix_contact_aliases_contact", "contact_aliases", ["contact_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_aliases_contact", table_name="contact_aliases")
    op.drop_table("contact_aliases")
    op.drop_index("ix_contacts_tg_user", table_name="contacts")
    op.drop_index("ix_contacts_user_name", table_name="contacts")
    op.drop_table("contacts")
