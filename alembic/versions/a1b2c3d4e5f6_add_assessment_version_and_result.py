"""add assessment_version and result to assessments

Adds versioning + full structured recommendation storage to the assessments
table. Both columns are safe to add online:

  * ``assessment_version`` is NOT NULL but ships with a server_default of 'v1',
    so existing rows are backfilled implicitly without a data migration.
  * ``result`` is nullable (older rows simply have no stored ranking).

Revision ID: a1b2c3d4e5f6
Revises: 88ffcc5f4135
Create Date: 2025-07-27 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "88ffcc5f4135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column(
            "assessment_version",
            sa.String(length=20),
            nullable=False,
            server_default="v1",
        ),
    )
    op.add_column(
        "assessments",
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessments", "result")
    op.drop_column("assessments", "assessment_version")
