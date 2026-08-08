"""Add state + city snapshot to inquiries

Captured from the facility the user is inquiring about (facilities already
carry state/city), so a lead knows WHERE it is without another join. The admin
view combines these into a single `location` field to match chatbot leads.

Revision ID: f5c3d4e6a2b1
Revises: e4b2c3d5f8a1
"""
from alembic import op
import sqlalchemy as sa

revision = "f5c3d4e6a2b1"
down_revision = "e4b2c3d5f8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inquiries", sa.Column("state", sa.String(length=50), nullable=True))
    op.add_column("inquiries", sa.Column("city", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("inquiries", "city")
    op.drop_column("inquiries", "state")
