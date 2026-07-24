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

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# api_gateway.persistence and shared.audit_ledger each own a SEPARATE
# DeclarativeBase (by design — see their module docstrings: independent
# schema evolution, possibly separate DBs/schemas per deployment). Their
# tables were create_all-only until this migration; importing them here
# registers their metadata below so autogenerate can see future drift too.
from api_gateway import persistence as _gateway_persistence  # noqa: F401
from evolution import models as _evolution_models  # noqa: F401

# ── Register all AURA model metadata ──────────────────────────────
# Importing these modules side-effects-registers their tables on
# Base.metadata. Add new model modules here as they are introduced.
from metadata_store import models as _metadata_models  # noqa: F401
from metadata_store.db import DATABASE_URL, Base
from shared import audit_ledger as _audit_ledger_models  # noqa: F401
from uasr import models as _uasr_models  # noqa: F401

# Alembic Config object — values come from alembic.ini.
config = context.config

# Inject the runtime DATABASE_URL so migrations target the same store.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    # disable_existing_loggers=False: the stdlib default (True) disables every
    # already-created logger (e.g. "aura.shared.tasks") when alembic configures
    # its logging. In a single-process pytest run that silently poisons later
    # tests asserting on log output, and would drop app logs if migrations ever
    # ran in the app's process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# A sequence of MetaData objects (supported by Alembic >=1.10, pinned
# >=1.13 in requirements.txt) so autogenerate diffs against all three
# independently-owned schemas in one pass.
target_metadata = [
    Base.metadata,
    _gateway_persistence.Base.metadata,
    _audit_ledger_models.Base.metadata,
]


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
        # Server-default comparison is disabled: the ORM models declare
        # Python-side `default=` (applied by SQLAlchemy at INSERT), while the
        # migrations carry DB-level `server_default=`. Both are intentional, but
        # autogenerate cannot reconcile them and emits phantom `modify_default`
        # ops on every such column. Structural drift (missing columns / tables /
        # indexes / type changes) is still caught via compare_type + the default
        # table/column/index comparison.
        compare_server_default=False,
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
        # Server-default comparison is disabled: the ORM models declare
        # Python-side `default=` (applied by SQLAlchemy at INSERT), while the
        # migrations carry DB-level `server_default=`. Both are intentional, but
        # autogenerate cannot reconcile them and emits phantom `modify_default`
        # ops on every such column. Structural drift (missing columns / tables /
        # indexes / type changes) is still caught via compare_type + the default
        # table/column/index comparison.
        compare_server_default=False,
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
