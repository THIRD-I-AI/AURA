"""
Alembic migration environment for AURA.

- Pulls the database URL from `metadata_store.db.DATABASE_URL` (which already
  honours METADATA_DATABASE_URL / defaults to aiosqlite) so there is one
  source of truth across runtime and migrations.
- Imports the model modules so every Base-registered table shows up in
  `target_metadata` — this is what `--autogenerate` diffs against.
- Runs async engines synchronously via `connection.run_sync()`, which is
  required because AURA uses async SQLAlchemy drivers.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Register all AURA model metadata ──────────────────────────────
# Importing these modules side-effects-registers their tables on
# Base.metadata. Add new model modules here as they are introduced.
from metadata_store import models as _metadata_models  # noqa: F401
from metadata_store.db import DATABASE_URL, Base
from evolution import models as _evolution_models  # noqa: F401
from uasr import models as _uasr_models  # noqa: F401

# Alembic Config object — values come from alembic.ini.
config = context.config

# Inject the runtime DATABASE_URL so migrations target the same store.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite") or "aiosqlite" in url


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (emit to stdout)."""
    url = config.get_main_option("sqlalchemy.url") or DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(url),
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    url = str(connection.engine.url)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(url),  # SQLite needs batch mode for ALTERs
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
