"""
Alembic migration environment for AURA.

- Pulls the database URL from `metadata_store.db.DATABASE_URL` (which already
  honours METADATA_DATABASE_URL / defaults to aiosqlite) so there is one
  source of truth across runtime and migrations — for deployments where
  GATEWAY_DATABASE_URL / AURA_LEDGER_DATABASE_URL coincide with it (the
  Postgres production topology, where all three point at the same `db`
  service). When they DON'T coincide (the local/free-tier SQLite fallback,
  where each resolves to its own file), running every migration against only
  the metadata URL silently misses `api_gateway.persistence`'s and
  `shared.audit_ledger`'s tables entirely — see BUG-044. `_run_split_db_pass`
  below closes that gap for the split case without changing anything about
  the single-URL path, which stays exactly as before.
- Imports the model modules so every Base-registered table shows up in
  `target_metadata` — this is what `--autogenerate` diffs against.
- Runs async engines synchronously via `connection.run_sync()`, which is
  required because AURA uses async SQLAlchemy drivers.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic.runtime.migration import MigrationContext
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
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


# ── BUG-044: split-database backfill ────────────────────────────────
#
# Only runs when GATEWAY_DATABASE_URL / AURA_LEDGER_DATABASE_URL genuinely
# differ from the metadata store's URL (the local/free-tier SQLite fallback
# topology). The primary pass above already migrated the metadata URL
# normally and correctly; this replays the SAME revision chain against each
# other distinct URL so their tables — created via `create_all()` at
# whatever point the app first touched them, per persistence.py's
# lazy-init pattern — catch up on every migration that came after.
#
# A historical revision predating a given URL's introduction into this
# multi-database setup is expected to already match what create_all() built
# (same columns/tables, just never Alembic-tracked): running it hits a
# SQLite "already exists"/"duplicate column" error, which is treated as
# proof the revision is satisfied — rolled back to a savepoint and the
# version table advanced without re-executing it — rather than a real
# failure. Any OTHER error still propagates; this only swallows the exact
# idempotency-class errors that prove the schema already matches.
_IDEMPOTENT_ERROR_MARKERS = ("already exists", "duplicate column name")


def _stamp(connection: Connection, revision: str) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    connection.exec_driver_sql("DELETE FROM alembic_version")
    connection.exec_driver_sql(
        "INSERT INTO alembic_version (version_num) VALUES (:v)", {"v": revision}
    )


def _do_split_db_pass(connection: Connection) -> None:
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config)
    mc = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": target_metadata,
            "compare_type": True,
            "compare_server_default": False,
            # Split-database passes only ever run against SQLite (the
            # deployment topology this backfill exists for) — batch mode
            # is required there for ALTER-style operations.
            "render_as_batch": True,
        },
    )
    already_applied = mc.get_current_heads()

    # Oldest-first: walk_revisions(base, head) yields newest-to-oldest.
    for rev in reversed(list(script.walk_revisions("base", "head"))):
        if rev.revision in already_applied:
            continue
        module = script.get_revision(rev.revision).module
        savepoint = connection.begin_nested()
        try:
            # Operations.context() binds this instance as the target of the
            # module-level `op.*` proxy calls inside module.upgrade() — a
            # bare Operations(mc) instance is never wired to that proxy.
            with Operations.context(mc):
                module.upgrade()
        except OperationalError as exc:
            savepoint.rollback()
            msg = str(getattr(exc, "orig", exc)).lower()
            if not any(marker in msg for marker in _IDEMPOTENT_ERROR_MARKERS):
                raise
            # Schema already matches (create_all() built it) — advance the
            # version pointer without having actually re-run the DDL.
        else:
            savepoint.commit()
        _stamp(connection, rev.revision)
    connection.commit()


async def _run_split_db_pass(url: str) -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_split_db_pass)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

    other_urls = {
        _gateway_persistence.database_url(),
        _audit_ledger_models.database_url(),
    }
    other_urls.discard(DATABASE_URL)
    for url in sorted(other_urls):
        asyncio.run(_run_split_db_pass(url))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
