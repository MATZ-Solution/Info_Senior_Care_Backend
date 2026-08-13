"""Add user_name + user_email snapshot to inquiries

So each inquiry/lead carries the requester's name and email captured at submit
time (copied from their profile), making a lead self-contained without joining
back to profiles. The user_id FK still links to the live profile.

Revision ID: e4b2c3d5f8a1
Revises: d3a1b2c4e5f7
"""
from alembic import op
import sqlalchemy as sa

revision = "e4b2c3d5f8a1"
down_revision = "d3a1b2c4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inquiries", sa.Column("user_name", sa.String(length=300), nullable=True))
    op.add_column("inquiries", sa.Column("user_email", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("inquiries", "user_email")
    op.drop_column("inquiries", "user_name")
