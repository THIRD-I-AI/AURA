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


def _run_script(*args: str, timeout: int = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **env} if env else None,
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

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db), "--delete-login")

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert MANUAL_VERIFY_EMAIL in result.stdout
    assert NAMESPACED_MATCH_SOURCE_ID in result.stdout

    # 1 user row + (5 rows * 4 tables) = 21 rows -- NOT "1 key + 1 key = 2",
    # which is what the DISTINCT-count regression would have reported.
    assert "Dry run only: 21 row(s) and 0 file(s) would be deleted." in result.stdout

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

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db), "--confirm", "--delete-login")

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

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(missing_uasr_db), "--confirm", "--delete-login")

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



# --- BUG-171: a cleanup that inspected nothing must not say "Nothing to do." ---
#
# The documented procedure is "run it on the box", but the defaults
# (/data/state/*.db) are the CONTAINER's paths. On the host the same files sit in
# the Docker volume, so both databases were skipped as "not found" and the script
# still printed "Nothing to do." with exit 0 -- reading as a clean box while
# leaving every leftover verify_ row (and the report) unexamined.


def test_when_neither_database_can_be_opened_it_says_nothing_was_checked_and_fails(tmp_path: Path) -> None:
    result = _run_script(
        "--metadata-db", str(tmp_path / "no_metadata.db"),
        "--uasr-db", str(tmp_path / "no_uasr.db"),
    )

    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "NOTHING WAS CHECKED" in result.stdout
    assert "Nothing to do." not in result.stdout
    # Tells the operator how to fix it rather than just refusing.
    assert "--metadata-db" in result.stdout and "--uasr-db" in result.stdout


def test_missing_default_paths_fall_back_to_the_docker_volume_copy(tmp_path: Path) -> None:
    """No flags: the container defaults do not exist on this machine, so the
    script must look in the deployment's Docker-volume state directory."""
    _seed_metadata_db(tmp_path / "metadata.db", [REAL_EMAIL, MANUAL_VERIFY_EMAIL])
    _seed_uasr_db(tmp_path / "uasr.db", {REAL_SOURCE_ID: 1, NAMESPACED_MATCH_SOURCE_ID: 2})

    result = _run_script("--delete-login", env={"AURA_CLEANUP_FALLBACK_STATE_DIR": str(tmp_path)})

    assert result.returncode == 0, result.stderr
    assert "[note]" in result.stdout and str(tmp_path) in result.stdout
    assert "NOTHING WAS CHECKED" not in result.stdout
    assert "9 row(s) and 0 file(s) would be deleted" in result.stdout  # 1 login + 2 rows x 4 UASR tables
    assert "verify-uasr@example.com" in result.stdout
    assert NAMESPACED_MATCH_SOURCE_ID in result.stdout
    # Dry run: nothing was touched.
    assert MANUAL_VERIFY_EMAIL in _users_snapshot(tmp_path / "metadata.db")


def test_an_explicit_path_is_never_replaced_by_the_fallback(tmp_path: Path) -> None:
    """--metadata-db pointed at a missing file is how an operator deliberately
    skips the metadata half (leaving the persistent test login alone). The
    fallback must not quietly substitute a real database for it."""
    _seed_metadata_db(tmp_path / "metadata.db", [MANUAL_VERIFY_EMAIL])
    _seed_uasr_db(tmp_path / "uasr.db", {NAMESPACED_MATCH_SOURCE_ID: 1})

    result = _run_script(
        "--metadata-db", str(tmp_path / "skip_me.db"),
        "--uasr-db", str(tmp_path / "uasr.db"),
        "--confirm",
        env={"AURA_CLEANUP_FALLBACK_STATE_DIR": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "[skip] database not found" in result.stdout
    assert "[note]" not in result.stdout
    assert MANUAL_VERIFY_EMAIL in _users_snapshot(tmp_path / "metadata.db"), "the skipped half was modified"
    assert NAMESPACED_MATCH_SOURCE_ID not in _table_source_ids(tmp_path / "uasr.db", "uasr_drift_events")


# ── The shared login is kept; leftover verify uploads are removed ──────────────


def _seed_uploads(root: Path) -> dict[str, Path]:
    files = {
        "verify_csv": root / "tenant-a" / "verify_05d29aeb_upload.csv",
        "verify_xlsx": root / "tenant-a" / "verify_05d29aeb_xlsx.xlsx",
        "customer": root / "tenant-a" / "sales_2025.csv",
        "near_miss": root / "tenant-b" / "verify_report.csv",
        "too_deep": root / "tenant-b" / "nested" / "verify_0829aaf7_upload.csv",
    }
    for f in files.values():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a,b\n1,2\n")
    return files


def test_by_default_the_shared_login_is_kept(tmp_path: Path) -> None:
    metadata_db, uasr_db = tmp_path / "metadata.db", tmp_path / "uasr.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL, MANUAL_VERIFY_EMAIL])
    _seed_uasr_db(uasr_db, {NAMESPACED_MATCH_SOURCE_ID: 1})

    result = _run_script("--metadata-db", str(metadata_db), "--uasr-db", str(uasr_db),
                         "--uploads-dir", str(tmp_path / "none"), "--confirm")

    assert result.returncode == 0, result.stderr
    assert MANUAL_VERIFY_EMAIL in _users_snapshot(metadata_db)
    assert f"kept ({MANUAL_VERIFY_EMAIL})" in result.stdout


def test_verify_uploads_are_listed_then_deleted_and_nothing_else_is(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    files = _seed_uploads(uploads)
    common = ("--metadata-db", str(tmp_path / "absent.db"), "--uasr-db", str(tmp_path / "absent2.db"),
              "--uploads-dir", str(uploads))

    dry = _run_script(*common)
    assert dry.returncode == 0, dry.stderr
    assert "0 row(s) and 2 file(s) would be deleted" in dry.stdout
    assert all(f.exists() for f in files.values())

    done = _run_script(*common, "--confirm")
    assert done.returncode == 0, done.stderr
    assert "2 of 2 file(s) deleted" in done.stdout
    assert not files["verify_csv"].exists() and not files["verify_xlsx"].exists()
    assert files["customer"].exists() and files["near_miss"].exists() and files["too_deep"].exists()


def test_an_unreadable_uploads_fallback_is_skipped_not_a_crash(tmp_path: Path, monkeypatch) -> None:
    # CI runners have a root-only /var/lib/docker: the fallback exists but cannot be read.
    import importlib.util

    spec = importlib.util.spec_from_file_location("cleanup_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cleanup_mod", mod)  # dataclasses look the module up
    spec.loader.exec_module(mod)

    def denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(mod.Path, "exists", denied)
    monkeypatch.setattr(mod.Path, "is_dir", denied)
    assert mod.resolve_db_path("/data/uploads", "/data/uploads", "uploads", fallback_dir=str(tmp_path)) == "/data/uploads"
    assert mod.find_upload_matches(str(tmp_path)) == []


# ── BUG-580: tenant-scoped ids, the state store, dashboards and run accounts ───
#
# On production the script found nothing although leftover verify data was there.
# UASR stores a run's source as "<tenant>::verify_<hex>_uasr" (uasr/tenancy.py), and
# the pattern required a bare id. uasr_state.db, uasr_repair_watches, schema_columns,
# gateway_dashboards and the live-check loop's own accounts were never looked at.

TENANT = "3f2b8c1e-9a4d-4e7f-b2c6-0d1e2f3a4b5c"
PREFIXED_UASR_ID = f"{TENANT}::verify_a1b2c3d4_uasr"
# Never matched: a customer's own ids, and a near-miss of each part of the shape.
UASR_NEAR_MISSES = [
    "verify_report",
    "verify_1234567_uasr",  # 7 hex digits
    "xverify_a1b2c3d4_uasr",
    "verify_A1B2C3D4_uasr",  # uppercase hex
    "verify_a1b2c3d4_uasr\n",  # `$` alone matches before a trailing newline
    f"{TENANT}::verify_report",
    f"{TENANT}::verify_1234567_uasr",
    f"{TENANT}::xverify_a1b2c3d4_uasr",
    f"{TENANT}::verify_A1B2C3D4_uasr",
    "::verify_a1b2c3d4_uasr",  # empty tenant
    f"{TENANT}::folder::verify_a1b2c3d4_uasr",  # two prefixes: UASR scopes a run's id once
    f"{TENANT}::verify_a1b2c3d4_upload",  # another label
]
RUN_ACCOUNTS = [
    "verify_0badf00d@example.com",
    "verify-loop-1732000000@example.com",
    "loop-verify@example.com",
    "aura-live-verify-bot+1732000000@dataaura.local",
]
ACCOUNT_NEAR_MISSES = [
    "verify_report@example.com",
    "verify_0BADF00D@example.com",
    "verify_0badf00@example.com",
    "verify_0badf00d@company.com",
    "verify-loop-12a@example.com",
    "verify-loop-@example.com",
    "verify-loop-1@example.org",
    "xloop-verify@example.com",
    "Loop-verify@example.com",
    "loop-verify@example.com.evil",
    "aura-live-verify-bot@dataaura.local",
    "aura-live-verify-bot+1@dataaura.local.example",
]


def _seed_rows(path: Path, table: str, column: str, rows_per_value: dict[str, int]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {column} TEXT, payload TEXT)")
        for value, n in rows_per_value.items():
            conn.executemany(f"INSERT INTO {table} ({column}, payload) VALUES (?, ?)", [(value, f"row-{i}") for i in range(n)])
        conn.commit()
    finally:
        conn.close()


def _seed_state_db(path: Path, source_ids: list[str]) -> None:
    # The DDL of uasr/state_store.py SqliteStateStore.
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE uasr_source_state (source_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        conn.executemany("INSERT INTO uasr_source_state VALUES (?, ?)", [(s, "{}") for s in source_ids])
        conn.commit()
    finally:
        conn.close()


def _seed_dashboards(path: Path, names: list[str]) -> None:
    # The columns of api_gateway/persistence.py DashboardRow: tiles are inline JSON.
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE gateway_dashboards (id VARCHAR(64) PRIMARY KEY, workspace_id VARCHAR(64) NOT NULL, "
            "name VARCHAR(255) NOT NULL, description TEXT, tiles_json TEXT NOT NULL DEFAULT '[]', "
            "created_at VARCHAR(64) NOT NULL, created_ts FLOAT NOT NULL, updated_at VARCHAR(64) NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO gateway_dashboards VALUES (?, ?, ?, NULL, '[]', '2026-11-20', 0, '2026-11-20')",
            [(f"d{i}", TENANT, name) for i, name in enumerate(names)],
        )
        conn.commit()
    finally:
        conn.close()


def _column_values(path: Path, table: str, column: str) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(r[0] for r in conn.execute(f"SELECT {column} FROM {table}"))
    finally:
        conn.close()


def _db_args(tmp_path: Path, **paths: Path) -> list[str]:
    """Every location flag, set to `paths` or to a file that does not exist, so the
    test never reads a default location."""
    flags = {"metadata_db": "--metadata-db", "uasr_db": "--uasr-db", "uasr_state_db": "--uasr-state-db",
             "gateway_db": "--gateway-db", "uploads_dir": "--uploads-dir"}
    args: list[str] = []
    for key, flag in flags.items():
        args += [flag, str(paths.get(key, tmp_path / f"absent_{key}"))]
    return args


def test_bug580_tenant_prefixed_uasr_ids_are_deleted_and_near_misses_survive(tmp_path: Path) -> None:
    uasr_db = tmp_path / "uasr.db"
    _seed_uasr_db(uasr_db, {PREFIXED_UASR_ID: 3, NAMESPACED_MATCH_SOURCE_ID: 1, REAL_SOURCE_ID: 1,
                            **{s: 1 for s in UASR_NEAR_MISSES}})

    # Only the flags the script has always had, so this fails on the old pattern, not on a flag.
    result = _run_script("--metadata-db", str(tmp_path / "absent.db"), "--uasr-db", str(uasr_db),
                         "--uploads-dir", str(tmp_path / "none"), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "uasr.db: OK -- 16 row(s) deleted." in result.stdout  # (3 + 1) rows x 4 tables
    for table in ("uasr_drift_events", "uasr_recovery_records", "uasr_distribution_snapshots", "uasr_batch_embeddings"):
        assert _table_source_ids(uasr_db, table) == sorted([REAL_SOURCE_ID, *UASR_NEAR_MISSES])


def test_bug580_uasr_state_db_and_repair_watches_are_covered(tmp_path: Path) -> None:
    uasr_db, state_db = tmp_path / "uasr.db", tmp_path / "uasr_state.db"
    # Only the newest UASR table exists; the four others are skipped, not fatal.
    _seed_rows(uasr_db, "uasr_repair_watches", "source_id", {PREFIXED_UASR_ID: 2, f"{TENANT}::orders": 1})
    _seed_state_db(state_db, [PREFIXED_UASR_ID, NAMESPACED_MATCH_SOURCE_ID, f"{TENANT}::orders", *UASR_NEAR_MISSES])

    result = _run_script(*_db_args(tmp_path, uasr_db=uasr_db, uasr_state_db=state_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "[skip] table not found: uasr_drift_events" in result.stdout
    assert "uasr.db: OK -- 2 row(s) deleted." in result.stdout
    assert "uasr_state.db: OK -- 2 row(s) deleted." in result.stdout
    assert _column_values(uasr_db, "uasr_repair_watches", "source_id") == [f"{TENANT}::orders"]
    assert _column_values(state_db, "uasr_source_state", "source_id") == sorted([f"{TENANT}::orders", *UASR_NEAR_MISSES])


def test_bug580_upload_profiles_bare_and_tenant_prefixed_are_deleted(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL])
    # schema_source_id() prefixes tenant_slug(tenant), which is "default" for no tenant.
    upload_ids = ["verify_05d29aeb_upload", f"{TENANT}::verify_05d29aeb_upload", "default::verify_0829aaf7_upload"]
    kept = [f"{TENANT}::sales_2025", "verify_report", f"{TENANT}::verify_report", f"{TENANT}::verify_05d29aeb_uasr",
            f"{TENANT}::verify_05D29AEB_upload", f"{TENANT}::xverify_05d29aeb_upload", "verify_05d29ae_upload",
            "verify_05d29aeb_xlsx"]
    _seed_rows(metadata_db, "schema_columns", "source_id", {**{s: 3 for s in upload_ids}, **{s: 1 for s in kept}})

    result = _run_script(*_db_args(tmp_path, metadata_db=metadata_db), "--confirm")

    assert result.returncode == 0, result.stderr
    assert "metadata.db: OK -- 9 row(s) deleted." in result.stdout  # 3 columns x 3 profiles
    assert _column_values(metadata_db, "schema_columns", "source_id") == sorted(kept)
    assert _users_snapshot(metadata_db) == [REAL_EMAIL]


def test_bug580_verify_dashboards_are_deleted_by_exact_name_only(tmp_path: Path) -> None:
    gateway_db = tmp_path / "gateway.db"
    verify = ["verify_a1b2c3d4_dashboard", "verify_0badf00d_dashboard"]
    customer = ["Sales overview", "verify_report", "verify_a1b2c3d4_dashboard_v2", "my verify_a1b2c3d4_dashboard",
                "xverify_a1b2c3d4_dashboard", "verify_A1B2C3D4_dashboard", "verify_1234567_dashboard",
                f"{TENANT}::verify_a1b2c3d4_dashboard", "verify_a1b2c3d4_dashboard\n"]
    _seed_dashboards(gateway_db, verify + customer)
    args = _db_args(tmp_path, gateway_db=gateway_db)

    dry = _run_script(*args)
    assert dry.returncode == 0, dry.stderr
    assert "Dry run only: 2 row(s) and 0 file(s) would be deleted." in dry.stdout
    assert _column_values(gateway_db, "gateway_dashboards", "name") == sorted(verify + customer)

    done = _run_script(*args, "--confirm")
    assert done.returncode == 0, done.stderr
    assert "gateway.db: OK -- 2 row(s) deleted." in done.stdout
    assert _column_values(gateway_db, "gateway_dashboards", "name") == sorted(customer)


def test_bug580_run_accounts_are_deleted_only_with_the_flag_and_the_fixed_login_is_kept(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    everyone = [REAL_EMAIL, MANUAL_VERIFY_EMAIL, *RUN_ACCOUNTS, *ACCOUNT_NEAR_MISSES]
    _seed_metadata_db(metadata_db, everyone)
    args = _db_args(tmp_path, metadata_db=metadata_db)

    without_flag = _run_script(*args, "--confirm")
    assert without_flag.returncode == 0, without_flag.stderr
    assert _users_snapshot(metadata_db) == sorted(everyone)

    with_flag = _run_script(*args, "--confirm", "--delete-run-accounts")
    assert with_flag.returncode == 0, with_flag.stderr
    assert f"kept ({MANUAL_VERIFY_EMAIL})" in with_flag.stdout
    assert "metadata.db: OK -- 4 row(s) deleted." in with_flag.stdout
    assert _users_snapshot(metadata_db) == sorted([REAL_EMAIL, MANUAL_VERIFY_EMAIL, *ACCOUNT_NEAR_MISSES])

    with_login = _run_script(*args, "--confirm", "--delete-run-accounts", "--delete-login")
    assert with_login.returncode == 0, with_login.stderr
    assert _users_snapshot(metadata_db) == sorted([REAL_EMAIL, *ACCOUNT_NEAR_MISSES])


def test_bug580_an_account_another_table_points_at_is_kept_and_the_reason_reported(tmp_path: Path) -> None:
    metadata_db = tmp_path / "metadata.db"
    _seed_metadata_db(metadata_db, [REAL_EMAIL, RUN_ACCOUNTS[0]])
    conn = sqlite3.connect(str(metadata_db))
    try:
        conn.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id))")
        conn.execute("INSERT INTO api_keys (user_id) SELECT id FROM users WHERE email = ?", (RUN_ACCOUNTS[0],))
        conn.commit()
    finally:
        conn.close()

    result = _run_script(*_db_args(tmp_path, metadata_db=metadata_db), "--confirm", "--delete-run-accounts")

    assert result.returncode == 0, result.stderr
    assert "[kept] 1 matched key(s) in users: referenced by api_keys.user_id" in result.stdout
    assert _users_snapshot(metadata_db) == sorted([REAL_EMAIL, RUN_ACCOUNTS[0]])


def test_bug580_dry_run_deletes_nothing_in_any_database(tmp_path: Path) -> None:
    dbs = {"metadata_db": tmp_path / "metadata.db", "uasr_db": tmp_path / "uasr.db",
           "uasr_state_db": tmp_path / "uasr_state.db", "gateway_db": tmp_path / "gateway.db"}
    _seed_metadata_db(dbs["metadata_db"], [REAL_EMAIL, MANUAL_VERIFY_EMAIL, *RUN_ACCOUNTS])
    _seed_rows(dbs["metadata_db"], "schema_columns", "source_id", {f"{TENANT}::verify_05d29aeb_upload": 3})
    _seed_uasr_db(dbs["uasr_db"], {PREFIXED_UASR_ID: 2})
    _seed_rows(dbs["uasr_db"], "uasr_repair_watches", "source_id", {PREFIXED_UASR_ID: 1})
    _seed_state_db(dbs["uasr_state_db"], [PREFIXED_UASR_ID])
    _seed_dashboards(dbs["gateway_db"], ["verify_a1b2c3d4_dashboard"])
    before = {name: path.read_bytes() for name, path in dbs.items()}

    result = _run_script(*_db_args(tmp_path, **dbs), "--delete-login", "--delete-run-accounts")

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    # 5 accounts + 3 profile rows + (2 x 4 + 1) UASR rows + 1 state row + 1 dashboard.
    assert "Dry run only: 19 row(s) and 0 file(s) would be deleted." in result.stdout
    for value in (PREFIXED_UASR_ID, "verify_a1b2c3d4_dashboard", f"{TENANT}::verify_05d29aeb_upload", *RUN_ACCOUNTS):
        assert value in result.stdout
    assert {name: path.read_bytes() for name, path in dbs.items()} == before
