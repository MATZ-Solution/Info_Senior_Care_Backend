"""add source_uuid and facility_type_category to facilities

Revision ID: 2db03c0ec147
Revises: 8670630250e7
Create Date: 2026-07-13 08:41:37.213871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2db03c0ec147'
down_revision: Union[str, None] = '8670630250e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('facilities', sa.Column('source_uuid', sa.String(length=64), nullable=True))
    op.add_column('facilities', sa.Column('facility_type_category', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_facilities_facility_type_category'), 'facilities', ['facility_type_category'], unique=False)
    op.create_index(op.f('ix_facilities_source_uuid'), 'facilities', ['source_uuid'], unique=False)
    # NOTE: autogenerate also tried to DROP ix_facilities_ccn_unique,
    # ix_facilities_dedup_hash_unique, and ix_facilities_geo here -- those
    # were intentionally removed. They're real indexes added by
    # scripts/patch_migration.py using raw SQL (partial unique indexes and
    # a GiST geo index), not declared via SQLAlchemy model metadata, so
    # autogenerate can't "see" them and assumes they're stale. They are
    # NOT stale -- dropping them would silently break ccn/dedup_hash
    # uniqueness enforcement and radius/geo search performance.

    # One-time backfill: rows imported by an EARLIER version of
    # scripts/import_facilities.py never had this column -- but that script
    # always stashed the same value inside extra_attributes.source_uuid "for
    # traceability". Recover it into the new first-class column now, so the
    # very next import can immediately match existing rows by source_uuid
    # (the reliable key) instead of falling back to dedup_hash (which
    # changes whenever a re-imported file cleans up facility_type text,
    # causing false "new facility" inserts/duplicates instead of updates).
    op.execute(
        """
        UPDATE facilities
        SET source_uuid = extra_attributes->>'source_uuid'
        WHERE source_uuid IS NULL
          AND extra_attributes->>'source_uuid' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_facilities_source_uuid'), table_name='facilities')
    op.drop_index(op.f('ix_facilities_facility_type_category'), table_name='facilities')
    op.drop_column('facilities', 'facility_type_category')
    op.drop_column('facilities', 'source_uuid')
