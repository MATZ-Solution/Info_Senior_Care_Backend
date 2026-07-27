"""
Alembic migration environment.

IMPORTANT: migrations run against MIGRATION_DATABASE_URL (a direct, non-
pooled connection) rather than DATABASE_URL (the pgbouncer transaction-mode
pooler used by the running app). DDL statements (CREATE INDEX, ALTER TABLE,
CREATE EXTENSION) don't play well with transaction-mode pooling -- some
poolers reject or misbehave on session-level statements. Keep this
separation even though it means two connection strings in .env.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402

# Import all models so Base.metadata is fully populated for autogenerate.
from app.models import (  # noqa: E402,F401
    Facility,
    NursingHomeDetails,
    HomeHealthDetails,
    FacilityServices,
    Profile,
    SavedFacility,
    Inquiry,
    Assessment,
    Resource,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_migration_url() -> str:
    # MIGRATION_DATABASE_URL is a plain postgresql:// URL (psycopg2 driver,
    # synchronous) -- Alembic itself runs sync regardless of the app being async.
    return settings.MIGRATION_DATABASE_URL


def run_migrations_offline() -> None:
    url = _sync_migration_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_sync_migration_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
