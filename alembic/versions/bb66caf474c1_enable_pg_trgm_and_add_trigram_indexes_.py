"""enable pg_trgm and add trigram indexes for fuzzy search

Revision ID: bb66caf474c1
Revises: 2db03c0ec147
Create Date: 2026-07-13 09:41:06.023252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bb66caf474c1'
down_revision: Union[str, None] = '2db03c0ec147'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enables typo-tolerant fuzzy text search (similarity(), % operator) --
    # splits text into overlapping 3-character chunks ("trigrams") so
    # 'nurshing' still matches 'nursing' even though it's not an exact
    # substring or a simple case/format difference.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram indexes -- without these, similarity()/% on 60k+ rows
    # would do a full table scan on every search request.
    op.execute(
        "CREATE INDEX ix_facilities_name_trgm ON facilities "
        "USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_facilities_city_trgm ON facilities "
        "USING gin (city gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_facilities_facility_type_trgm ON facilities "
        "USING gin (facility_type gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_facilities_facility_type_category_trgm ON facilities "
        "USING gin (facility_type_category gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_facilities_facility_type_category_trgm")
    op.execute("DROP INDEX IF EXISTS ix_facilities_facility_type_trgm")
    op.execute("DROP INDEX IF EXISTS ix_facilities_city_trgm")
    op.execute("DROP INDEX IF EXISTS ix_facilities_name_trgm")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
