"""Add facility_name + facility_type snapshot to inquiries

So every inquiry/lead records WHICH facility the user asked about, in a
human-readable way, without having to join back to facilities. These are a
point-in-time snapshot captured at submit (see endpoints/inquiries.py) -- the
facility_id FK still links to the live row.

Revision ID: d3a1b2c4e5f7
Revises: c7f2a9d4e8b1
"""
from alembic import op
import sqlalchemy as sa

revision = "d3a1b2c4e5f7"
down_revision = "c7f2a9d4e8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inquiries", sa.Column("facility_name", sa.String(length=500), nullable=True))
    op.add_column("inquiries", sa.Column("facility_type", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("inquiries", "facility_type")
    op.drop_column("inquiries", "facility_name")
