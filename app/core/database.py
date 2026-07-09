# """
# Database engine and session management.

# Uses SQLAlchemy 2.0 async with asyncpg. Pool settings are deliberately
# conservative per-instance because in production this connects through the
# Supabase pooler (pgbouncer, transaction mode) which has its own connection
# ceiling shared across ALL app instances. If you run N app instances, total
# pool usage = N * (DB_POOL_SIZE + DB_MAX_OVERFLOW) -- keep that under the
# pooler's configured limit.
# """
# import logging
# from contextlib import asynccontextmanager
# from typing import AsyncIterator

# from sqlalchemy.ext.asyncio import (
#     AsyncSession,
#     async_sessionmaker,
#     create_async_engine,
# )
# from sqlalchemy.orm import DeclarativeBase

# from app.core.config import settings

# logger = logging.getLogger("app.database")


# class Base(DeclarativeBase):
#     pass


# engine = create_async_engine(
#     settings.DATABASE_URL,
#     pool_size=settings.DB_POOL_SIZE,
#     max_overflow=settings.DB_MAX_OVERFLOW,
#     pool_pre_ping=True,      # avoids "stale connection" errors after idle periods
#     pool_recycle=1800,       # recycle connections every 30 min (pooler-friendly)
#     echo=False,
#     connect_args={
#         # CRITICAL when DATABASE_URL points at Supabase's pgbouncer pooler
#         # (port 6543, transaction mode): asyncpg caches "prepared
#         # statements" per physical connection, but pgbouncer in transaction
#         # mode can hand your session a DIFFERENT physical backend
#         # connection between statements. Without this, you'll intermittently
#         # hit "prepared statement ... already exists" / "does not exist"
#         # errors under load that are very confusing to debug. Harmless to
#         # leave on even against a direct (non-pooled) connection.
#         "statement_cache_size": 0,
#         "prepared_statement_cache_size": 0,
#     },
# )

# AsyncSessionLocal = async_sessionmaker(
#     bind=engine,
#     class_=AsyncSession,
#     expire_on_commit=False,
#     autoflush=False,
# )


# async def get_db() -> AsyncIterator[AsyncSession]:
#     """FastAPI dependency -- yields a request-scoped DB session."""
#     async with AsyncSessionLocal() as session:
#         try:
#             yield session
#         except Exception:
#             await session.rollback()
#             raise
#         finally:
#             await session.close()


# @asynccontextmanager
# async def db_session_ctx() -> AsyncIterator[AsyncSession]:
#     """Context-manager version for use outside request scope (scripts, jobs)."""
#     async with AsyncSessionLocal() as session:
#         try:
#             yield session
#             await session.commit()
#         except Exception:
#             await session.rollback()
#             raise
#         finally:
#             await session.close()


# async def check_db_connection() -> bool:
#     """Used by /health/ready -- cheap connectivity check."""
#     from sqlalchemy import text

#     try:
#         async with engine.connect() as conn:
#             await conn.execute(text("SELECT 1"))
#         return True
#     except Exception as exc:
#         logger.error("DB health check failed: %s", exc)
#         return False









"""
Database engine and session management.

Uses SQLAlchemy 2.0 async with asyncpg, through Supabase's pgbouncer pooler
(transaction mode, port 6543).

IMPORTANT -- pgbouncer + asyncpg prepared statements:
asyncpg's SQLAlchemy dialect names and prepares every statement server-side
via `connection.prepare(sql, name=...)`. In transaction-mode pgbouncer, the
physical backend connection behind any given client connection can change
between transactions, so two different client-side connections can each
try to create a same-named prepared statement on the same backend at
different times -- causing intermittent
`DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_N__"
already exists` under concurrent load. Setting `statement_cache_size=0`
alone does NOT fix this (that only controls asyncpg's own convenience-
method auto-caching, which SQLAlchemy's dialect bypasses entirely by
calling `.prepare()` directly).

The actual fix, per SQLAlchemy's own documentation
(see the "Prepared Statement Name with PGBouncer" section of
sqlalchemy/dialects/postgresql/asyncpg.py), is two parts:
  1. `poolclass=NullPool` -- don't pool connections client-side on top of
     pgbouncer's own pooling; let pgbouncer be the only pooling layer.
     (DB_POOL_SIZE / DB_MAX_OVERFLOW are therefore no longer used here --
     NullPool doesn't accept those parameters.)
  2. `prepared_statement_name_func` that generates a globally-unique name
     (uuid4) on every single prepare call, so collisions are structurally
     impossible regardless of which backend pgbouncer routes to.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger("app.database")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,
    connect_args={
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        "statement_cache_size": 0,
    },
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency -- yields a request-scoped DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def db_session_ctx() -> AsyncIterator[AsyncSession]:
    """Context-manager version for use outside request scope (scripts, jobs)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Used by /health/ready -- cheap connectivity check."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        return False