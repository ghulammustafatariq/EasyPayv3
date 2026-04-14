"""
app/db/migrations/env.py — Alembic async migration environment

Uses the async_database_url from settings (Critical Point 2 — Railway prefix fix).
All 16 models are imported so Alembic can auto-detect schema changes.

NOTE: % in the URL (e.g. %40 for @ in password) must be escaped as %% for
ConfigParser. We bypass config.set_main_option for the async engine and pass
the raw URL directly to create_async_engine to avoid this issue.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from app.core.config import settings
from app.db.base import Base
import app.models.database  # noqa: F401 — registers all 16 models with Base.metadata

config = context.config

# ── Validate DATABASE_URL early so Alembic fails with a clear message ─────────
_db_url = settings.async_database_url
if not _db_url:
    raise RuntimeError(
        "\n\n"
        "  DATABASE_URL is not configured.\n"
        "  Steps to fix:\n"
        "    1. Create a .env file in the project root (copy from .env.example)\n"
        "    2. Set:  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname\n"
        "       NOTE: If your password contains '@', encode it as '%40'\n"
        "             e.g. 'Mustafa@1122' -> 'Mustafa%401122'\n"
        "    3. Re-run: alembic revision --autogenerate -m 'description'\n"
    )

# ConfigParser uses % for interpolation — %% is the escaped literal percent.
# We set this for offline mode (which reads via config.get_main_option).
config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live connection)."""
    # get_main_option un-escapes %% -> % so the URL is reconstructed correctly
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against the live async PostgreSQL engine.

    We use _db_url directly (not via config.get_main_option) so that
    percent-encoded characters in the URL (e.g. %40 for @) are preserved
    and correctly decoded by SQLAlchemy's URL parser.
    """
    engine = create_async_engine(_db_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
