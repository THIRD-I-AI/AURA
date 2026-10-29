"""Audit round 11, migrations area: BUG-554, BUG-555.

Both drive `alembic upgrade` as a real subprocess against a temp SQLite file
(as tests/test_alembic_split_db_backfill.py does): env.py runs at import time
and keeps module-level state, so it cannot be exercised safely in-process.
All three database URLs point at the same file, which is the production
single-database topology, so env.py's split-database pass stays out of it.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

AURABACKEND_DIR = Path(__file__).resolve().parent.parent
# The interpreter running pytest, so the subprocess sees the same installed packages.
PYTHON = sys.executable

# What api_gateway/main.py's lifespan runs when its migration fails, plus the
# gateway persistence create_all it runs on every start (BUG-554).
_CREATE_ALL_FALLBACK = (
    "import asyncio\n"
    # The services import their models before init_db(), so create_all builds every table.
    "import metadata_store.models, uasr.models\n"
    "from metadata_store.db import get_engine, init_db\n"
    "from evolution.db import init_evolution_db\n"
    "from api_gateway.persistence import close_database, init_database\n"
    "async def main():\n"
    "    await init_db()\n"
    "    await init_evolution_db()\n"
    "    await init_database()\n"
    # Undisposed aiosqlite engines leave non-daemon threads that hang the exit.
    "    await close_database()\n"
    "    await get_engine().dispose()\n"
    "asyncio.run(main())\n"
)


def _env(db: Path) -> dict:
    url = f"sqlite+aiosqlite:///{db}"
    return {
        **os.environ,
        "METADATA_DATABASE_URL": url,
        "GATEWAY_DATABASE_URL": url,
        "AURA_LEDGER_DATABASE_URL": url,
    }


def _run(args, db: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [PYTHON, *args], cwd=str(AURABACKEND_DIR), env=_env(db),
        capture_output=True, text=True, timeout=300,
    )
    return result


def _upgrade(db: Path, target: str = "head") -> subprocess.CompletedProcess:
    return _run(["-m", "alembic", "upgrade", target], db)


def _create_all_fallback(db: Path) -> None:
    result = _run(["-c", _CREATE_ALL_FALLBACK], db)
    assert result.returncode == 0, result.stderr


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(AURABACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AURABACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _version(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _column_type(db: Path, table: str, column: str) -> str:
    conn = sqlite3.connect(str(db))
    try:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            if row[1] == column:
                return row[2]
    finally:
        conn.close()
    raise AssertionError(f"{table}.{column} not found")


def _ok(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, (
        f"alembic upgrade failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# ── BUG-554 ──────────────────────────────────────────────────────────────


def test_bug554_empty_db_upgrades_to_head(tmp_path):
    db = tmp_path / "aura.db"
    _ok(_upgrade(db))
    assert _version(db) == _head()


def test_bug554_partial_migration_then_create_all_fallback_still_reaches_head(tmp_path):
    # The registry's reproduction: migrated to a7b8c9d0e1f2, then a create_all
    # (gateway startup, or a worker whose migration lost the race) builds
    # gateway_synthetic_jobs / gateway_workspaces before b8c9d0e1f2a3 / c9d0e1f2a3b4 run.
    db = tmp_path / "aura.db"
    _ok(_upgrade(db, "a7b8c9d0e1f2"))
    _create_all_fallback(db)
    _ok(_upgrade(db))
    assert _version(db) == _head()


def test_bug554_create_all_first_db_upgrades_to_head(tmp_path):
    # metadata_store/main.py runs init_db() (create_all) unconditionally and
    # can win the startup race against the gateway's first migration, so the
    # initial revision meets tables that already exist.
    db = tmp_path / "aura.db"
    _create_all_fallback(db)
    _ok(_upgrade(db))
    assert _version(db) == _head()
    # A second run is a no-op, not an error.
    _ok(_upgrade(db))


# ── BUG-555 ──────────────────────────────────────────────────────────────


def test_bug555_migrated_dataset_profiles_id_fits_composite_id(tmp_path):
    db = tmp_path / "aura.db"
    _ok(_upgrade(db))
    assert _column_type(db, "dataset_profiles", "id").upper() == "VARCHAR(255)"


def test_bug555_model_id_column_fits_longest_composite_id():
    from metadata_store.models import DatasetProfile

    id_len = DatasetProfile.__table__.c.id.type.length
    workspace_len = DatasetProfile.__table__.c.workspace_id.type.length
    file_id_len = DatasetProfile.__table__.c.file_id.type.length
    # repository.upsert_dataset_profile builds f"{workspace_id or 'default'}::{file_id}".
    assert id_len >= workspace_len + len("::") + file_id_len
    # The connector-ingest id from the registry entry (uuid4 org + connector source id).
    org = "0d9f8b62-3c4e-4f7a-9b1d-2e6c8a5f7b13"
    source_id = f"{org}::postgres:orders"
    assert len(f"{org}::{source_id}") <= id_len
