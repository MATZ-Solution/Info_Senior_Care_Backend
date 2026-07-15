"""add certification_date secure_memory_care_beds cms_region specialty_notes source_state_abbr to facilities

Revision ID: 88ffcc5f4135
Revises: bb66caf474c1
Create Date: 2026-07-15 06:32:29.011695

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '88ffcc5f4135'
down_revision: Union[str, None] = 'bb66caf474c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('facilities', sa.Column('certification_date', sa.String(length=30), nullable=True))
    op.add_column('facilities', sa.Column('secure_memory_care_beds', sa.Integer(), nullable=True))
    op.add_column('facilities', sa.Column('cms_region', sa.Integer(), nullable=True))
    op.add_column('facilities', sa.Column('specialty_notes', sa.Text(), nullable=True))
    op.add_column('facilities', sa.Column('source_state_abbr', sa.String(length=2), nullable=True))
    # NOTE: autogenerate also tried to DROP ix_facilities_ccn_unique,
    # ix_facilities_dedup_hash_unique, ix_facilities_geo, and all 4 pg_trgm
    # GIN indexes here -- those were intentionally removed from this
    # migration. They're real indexes added via raw SQL (patch_migration.py
    # / migration bb66caf474c1), not declared via SQLAlchemy model
    # metadata, so autogenerate can't "see" them and assumes they're stale.
    # They are NOT stale -- dropping them would silently break ccn/
    # dedup_hash uniqueness enforcement, geo search, and all typo-tolerant
    # fuzzy search performance.


def downgrade() -> None:
    op.drop_column('facilities', 'source_state_abbr')
    op.drop_column('facilities', 'specialty_notes')
    op.drop_column('facilities', 'cms_region')
    op.drop_column('facilities', 'secure_memory_care_beds')
    op.drop_column('facilities', 'certification_date')
