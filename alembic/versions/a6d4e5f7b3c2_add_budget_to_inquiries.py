"""Add budget to inquiries

A dedicated budget field so it's captured separately from the free-text
message (previously budget info, if any, got buried in `message`). Surfaced as
its own column in the admin leads view.

Revision ID: a6d4e5f7b3c2
Revises: f5c3d4e6a2b1
"""
from alembic import op
import sqlalchemy as sa

revision = "a6d4e5f7b3c2"
down_revision = "f5c3d4e6a2b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inquiries", sa.Column("budget", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("inquiries", "budget")
