"""
Tests for scripts/cleanup_verify_resources.py, the standalone stdlib-sqlite3
script that finds and (with --confirm) deletes the namespaced "verify_*" test
resources left behind by verify_live_deployment.py.

Per CLAUDE.md's hard constraint ("NEVER mock an external call when a local
test environment exists"), these tests use real temporary SQLite files built
directly with sqlite3 -- no mocking of the database layer.

The script is meant to run standalone on the production box's bare python3
(no repo imports), so every test case here drives it exactly the way an
operator would: as a subprocess via `sys.executable scripts/cleanup_verify_resources.py
<args>`, never by importing and calling its internal functions directly.

Covers:
  1. Dry run (no --confirm) reports every real match with an accurate row
     count and leaves both DBs completely untouched.
  2. --confirm deletes exactly the matched rows -- real data and adversarial
     near-misses (values that merely contain "verify" as a substring but do
     not match the script's strict rules) survive.
  3. A missing/nonexistent DB path for one half does not crash the script --
     that half is skipped and the other half is still processed correctly.
  4. A source_id owning MANY rows reports and deletes the real row count,
     not just "1" for the one matched key -- this is the accuracy bug an
     earlier draft of this script had (DISTINCT-key count used as if it
     were a row count).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_PATH = Path(REPO_ROOT) / "scripts" / "cleanup_verify_resources.py"

# --- Fixture data -----------------------------------------------------------
REAL_EMAIL = "alice@company.com"
MANUAL_VERIFY_EMAIL = "verify-uasr@example.com"
# Not a real convention -- verify_live_deployment.py never namespaces emails
# per-run, so this must NOT be treated as a match by the script.
NEAR_MISS_EMAIL = "verify_a1b2c3d4_uasr@company.com"

REAL_SOURCE_ID = "prod_orders"
NAMESPACED_MATCH_SOURCE_ID = "verify_a1b2c3d4_uasr"
# Adversarial near-misses: contain "verify" and look plausible but must NOT
# match the strict ^verify_[0-9a-f]{8}_uasr$ pattern.
NEAR_MISS_SOURCE_ID_SUFFIX = "verify_a1b2c3d4_uasr_extra"
NEAR_MISS_SOURCE_ID_SHORT_HEX = "verify_a1b2_uasr"
NEAR_MISS_SOURCE_ID_UPPERCASE = "verify_A1B2C3D4_uasr"


def _seed_metadata_db(path: Path, emails: list[str]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL)")
        conn.executemany("INSERT INTO users (email) VALUES (?)", [(e,) for e in emails])
        conn.commit()
    finally:
        conn.close()


def _seed_uasr_db(path: Path, rows_per_source: dict[str, int]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for table in ("uasr_drift_events", "uasr_recovery_records",
                      "uasr_distribution_snapshots", "uasr_batch_embeddings"):
            conn.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, payload TEXT)"
            )
            for source_id, n in rows_per_source.items():
                conn.executemany(
                    f"INSERT INTO {table} (source_id, payload) VALUES (?, ?)",
                    [(source_id, f"row-{i}") for i in range(n)],
                )
        conn.commit()
    finally:
        conn.close()


def _users_snapshot(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(r[0] for r in conn.execute("SELECT email FROM users").fetchall())
    finally:
        conn.close()


def _table_source_ids(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(r[0] for r in conn.execute(f"SELECT source_id FROM {table}").fetchall())
    finally:
        conn.close()


def _run_script(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_dry_run_reports_accurate_row_counts_and_leaves_dbs_untouched(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    uasr_db = tmp_path / "uasr.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL, MANUAL_VERIFY_EMAIL, NEAR_MISS_EMAIL])
    # NAMESPACED_MATCH_SOURCE_ID owns 5 rows per table -- this is the
    # DISTINCT-key-vs-row-count regression check.
    _seed_uasr_db(uasr_db, {
        REAL_SOURCE_ID: 3,
        NAMESPACED_MATCH_SOURCE_ID: 5,
        NEAR_MISS_SOURCE_ID_SUFFIX: 2,
        NEAR_MISS_SOURCE_ID_SHORT_HEX: 2,
        NEAR_MISS_SOURCE_ID_UPPERCASE: 2,
    })

    before_users = _users_snapshot(metadata_db)
    before_tables = {
        t: _table_source_ids(uasr_db, t)
        for t in ("uasr_drift_events", "uasr_recovery_records", "uasr_distribution_snapshots", "uasr_batch_embeddings")
    }

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db))

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert MANUAL_VERIFY_EMAIL in result.stdout
    assert NAMESPACED_MATCH_SOURCE_ID in result.stdout

    # 1 user row + (5 rows * 4 tables) = 21 rows -- NOT "1 key + 1 key = 2",
    # which is what the DISTINCT-count regression would have reported.
    assert "Dry run only: 21 row(s) would be deleted." in result.stdout

    # Nothing was actually touched.
    assert _users_snapshot(metadata_db) == before_users
    after_tables = {
        t: _table_source_ids(uasr_db, t)
        for t in ("uasr_drift_events", "uasr_recovery_records", "uasr_distribution_snapshots", "uasr_batch_embeddings")
    }
    assert after_tables == before_tables


def test_confirm_deletes_only_matched_rows_and_reports_real_counts(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    uasr_db = tmp_path / "uasr.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL, MANUAL_VERIFY_EMAIL, NEAR_MISS_EMAIL])
    _seed_uasr_db(uasr_db, {
        REAL_SOURCE_ID: 3,
        NAMESPACED_MATCH_SOURCE_ID: 5,
        NEAR_MISS_SOURCE_ID_SUFFIX: 2,
        NEAR_MISS_SOURCE_ID_SHORT_HEX: 2,
        NEAR_MISS_SOURCE_ID_UPPERCASE: 2,
    })

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "metadata.db: OK -- 1 row(s) deleted." in result.stdout
    assert "uasr.db: OK -- 20 row(s) deleted." in result.stdout  # 5 rows * 4 tables

    remaining_emails = _users_snapshot(metadata_db)
    assert MANUAL_VERIFY_EMAIL not in remaining_emails
    assert REAL_EMAIL in remaining_emails
    assert NEAR_MISS_EMAIL in remaining_emails  # never matched -- no such convention
    assert len(remaining_emails) == 2

    for table in ("uasr_drift_events", "uasr_recovery_records", "uasr_distribution_snapshots", "uasr_batch_embeddings"):
        remaining = _table_source_ids(uasr_db, table)
        assert NAMESPACED_MATCH_SOURCE_ID not in remaining
        assert REAL_SOURCE_ID in remaining
        assert NEAR_MISS_SOURCE_ID_SUFFIX in remaining
        assert NEAR_MISS_SOURCE_ID_SHORT_HEX in remaining
        assert NEAR_MISS_SOURCE_ID_UPPERCASE in remaining
        # 3 real + 2 + 2 + 2 near-misses = 9 rows survive per table.
        assert len(remaining) == 9


def test_missing_metadata_db_skips_that_half_and_still_processes_uasr(tmp_path: Path) -> None:
    missing_metadata_db = tmp_path / "does_not_exist_metadata.db"
    uasr_db = tmp_path / "uasr.db"
    _seed_uasr_db(uasr_db, {REAL_SOURCE_ID: 1, NAMESPACED_MATCH_SOURCE_ID: 2})
    assert not missing_metadata_db.exists()

    result = _run_script("--metadata-db", str(missing_metadata_db), "--uasr-db", str(uasr_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "[skip] database not found" in result.stdout
    assert str(missing_metadata_db) in result.stdout
    assert "uasr.db: OK -- 8 row(s) deleted." in result.stdout  # 2 rows * 4 tables

    for table in ("uasr_drift_events", "uasr_recovery_records", "uasr_distribution_snapshots", "uasr_batch_embeddings"):
        remaining = _table_source_ids(uasr_db, table)
        assert NAMESPACED_MATCH_SOURCE_ID not in remaining
        assert REAL_SOURCE_ID in remaining


def test_missing_uasr_db_skips_that_half_and_still_processes_metadata(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    missing_uasr_db = tmp_path / "does_not_exist_uasr.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL, MANUAL_VERIFY_EMAIL])
    assert not missing_uasr_db.exists()

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(missing_uasr_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "[skip] database not found" in result.stdout
    assert str(missing_uasr_db) in result.stdout
    assert "metadata.db: OK -- 1 row(s) deleted." in result.stdout

    remaining = _users_snapshot(metadata_db)
    assert MANUAL_VERIFY_EMAIL not in remaining
    assert REAL_EMAIL in remaining


def test_confirm_abbreviation_is_rejected(tmp_path: Path) -> None:
    """--conf (or any other unambiguous prefix) must NOT be accepted as a
    stand-in for --confirm -- a typo must never silently trigger a delete."""
    metadata_db = tmp_path / "metadata.db"
    uasr_db = tmp_path / "uasr.db"
    _seed_metadata_db(metadata_db, [MANUAL_VERIFY_EMAIL])
    _seed_uasr_db(uasr_db, {NAMESPACED_MATCH_SOURCE_ID: 1})

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db), "--conf")

    assert result.returncode != 0, "an abbreviated flag must be rejected as an unknown argument, not accepted"
    # Nothing was deleted.
    assert MANUAL_VERIFY_EMAIL in _users_snapshot(metadata_db)


def test_no_matches_reports_nothing_to_do(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    uasr_db = tmp_path / "uasr.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL])
    _seed_uasr_db(uasr_db, {REAL_SOURCE_ID: 1})

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "Nothing to do." in result.stdout
    assert _users_snapshot(metadata_db) == [REAL_EMAIL]
