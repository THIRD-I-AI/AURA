"""BUG-044 — alembic/env.py's split-database backfill.

Regression proof for the bug found live on the production box: when
GATEWAY_DATABASE_URL / AURA_LEDGER_DATABASE_URL differ from
METADATA_DATABASE_URL (the local/free-tier SQLite fallback topology),
`alembic upgrade head` silently applied every migration only to the
metadata database, never touching gateway/ledger tables at all. This
drove `alembic upgrade head` as a real subprocess (matching exactly how
CI's "DB Migrations (Alembic)" job and the production redeploy invoke
it) against three distinct temp SQLite files, since env.py executes at
import time and can't be safely exercised in-process without leaking
module-level state across tests.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

AURABACKEND_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = AURABACKEND_DIR.parent / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _run_alembic_upgrade(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "alembic", "upgrade", "head"],
        cwd=str(AURABACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _table_columns(db_path: Path, table: str) -> set:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _alembic_version(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _tables(db_path: Path) -> set:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


@pytest.mark.skipif(not VENV_PYTHON.exists(), reason="repo-root venv not present")
def test_split_database_backfill_reaches_gateway_and_ledger_dbs(tmp_path):
    metadata_db = tmp_path / "metadata.db"
    gateway_db = tmp_path / "gateway.db"
    ledger_db = tmp_path / "ledger.db"

    # Simulate the exact production symptom: gateway_query_history already
    # exists (create_all()'s doing, pre-fix) but is missing workspace_id —
    # every other gateway table is absent entirely, as if the app never
    # happened to touch them yet.
    conn = sqlite3.connect(str(gateway_db))
    conn.execute(
        """
        CREATE TABLE gateway_query_history (
            id VARCHAR(64) NOT NULL PRIMARY KEY,
            prompt TEXT NOT NULL,
            sql TEXT NOT NULL,
            status VARCHAR(32) NOT NULL,
            rows INTEGER NOT NULL,
            execution_time FLOAT NOT NULL,
            timestamp VARCHAR(64) NOT NULL,
            created_ts FLOAT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    env = {
        **os.environ,
        "METADATA_DATABASE_URL": f"sqlite+aiosqlite:///{metadata_db}",
        "GATEWAY_DATABASE_URL": f"sqlite+aiosqlite:///{gateway_db}",
        "AURA_LEDGER_DATABASE_URL": f"sqlite+aiosqlite:///{ledger_db}",
    }

    result = _run_alembic_upgrade(env)
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # The one genuinely-missing column is now present.
    assert "workspace_id" in _table_columns(gateway_db, "gateway_query_history")

    # Tables that never existed on the gateway DB got created fresh by the
    # historical create_table migrations (not just stamped).
    gateway_tables = _tables(gateway_db)
    for expected in (
        "gateway_saved_queries", "gateway_dashboards", "gateway_connections",
        "gateway_chat_messages", "gateway_pipelines", "gateway_schema_context",
        "gateway_lineage_edges", "gateway_share_tokens", "gateway_file_metadata",
    ):
        assert expected in gateway_tables, f"{expected} missing from backfilled gateway.db"

    # The ledger database independently got its table too.
    assert "audit_ledger" in _tables(ledger_db)

    # The metadata database ran through the normal (unchanged) single-URL
    # path and has its own full schema, untouched by the backfill logic.
    metadata_tables = _tables(metadata_db)
    assert "users" in metadata_tables
    assert "semantic_models" in metadata_tables

    # Regression proof for the pollution bug this same fix introduced and
    # then corrected: migrations that only touch metadata-store-owned
    # tables (unrelated to gateway/ledger) must NEVER get created inside
    # gateway.db or ledger.db just because they don't already exist there.
    for unrelated in ("users", "semantic_models", "uasr_drift_events", "evolution_system_log"):
        assert unrelated not in gateway_tables, (
            f"{unrelated} is metadata-store-owned and must not exist in gateway.db"
        )
        assert unrelated not in _tables(ledger_db), (
            f"{unrelated} is metadata-store-owned and must not exist in ledger.db"
        )

    # Every split database must reach the SAME head as the metadata pass —
    # a revision belonging to neither database (e.g. an evolution-table
    # migration, which lives on the metadata Base) must be stamped past,
    # not left as a stopping point.
    head = _alembic_version(metadata_db)
    assert _alembic_version(gateway_db) == head
    assert _alembic_version(ledger_db) == head


@pytest.mark.skipif(not VENV_PYTHON.exists(), reason="repo-root venv not present")
def test_split_database_backfill_is_idempotent(tmp_path):
    """Running the backfill twice must not error — the second run should
    find everything already stamped and do nothing further."""
    metadata_db = tmp_path / "metadata.db"
    gateway_db = tmp_path / "gateway.db"
    ledger_db = tmp_path / "ledger.db"

    env = {
        **os.environ,
        "METADATA_DATABASE_URL": f"sqlite+aiosqlite:///{metadata_db}",
        "GATEWAY_DATABASE_URL": f"sqlite+aiosqlite:///{gateway_db}",
        "AURA_LEDGER_DATABASE_URL": f"sqlite+aiosqlite:///{ledger_db}",
    }

    first = _run_alembic_upgrade(env)
    assert first.returncode == 0, first.stderr
    second = _run_alembic_upgrade(env)
    assert second.returncode == 0, second.stderr

    assert "workspace_id" in _table_columns(gateway_db, "gateway_query_history")


@pytest.mark.skipif(not VENV_PYTHON.exists(), reason="repo-root venv not present")
def test_coincident_urls_use_the_single_pass_path_unchanged(tmp_path):
    """When all three *_DATABASE_URL env vars resolve to the SAME database
    (the Postgres production topology), the split-db backfill must never
    engage — this proves the co-located case is completely unaffected by
    BUG-044's fix."""
    shared_db = tmp_path / "shared.db"
    url = f"sqlite+aiosqlite:///{shared_db}"

    env = {
        **os.environ,
        "METADATA_DATABASE_URL": url,
        "GATEWAY_DATABASE_URL": url,
        "AURA_LEDGER_DATABASE_URL": url,
    }

    result = _run_alembic_upgrade(env)
    assert result.returncode == 0, result.stderr

    tables = _tables(shared_db)
    assert "gateway_query_history" in tables
    assert "audit_ledger" in tables
    assert "users" in tables
    assert "workspace_id" in _table_columns(shared_db, "gateway_query_history")
