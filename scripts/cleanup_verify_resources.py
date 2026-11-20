#!/usr/bin/env python3
"""cleanup_verify_resources.py -- delete namespaced test resources left behind
by scripts/verify_live_deployment.py.

Background
----------
verify_live_deployment.py runs live black-box checks against the production
gateway and UASR service, and deliberately does not clean up after itself.
Resources it creates via Verifier.ns() are namespaced:

    f"verify_{run_ns}_{label}"        where run_ns = uuid4().hex[:8]

e.g. the UASR self-heal check creates a source_id of the shape
"verify_a1b2c3d4_uasr", which UASR stores inside the caller's tenant as
"<tenant>::verify_a1b2c3d4_uasr" (uasr/tenancy.py). The script itself never
creates login accounts -- every run uses the one fixed
STAGING_EMAIL/STAGING_PASSWORD login, "verify-uasr@example.com" -- but the
live-check loop that drives it registers throwaway accounts of four known
shapes (RUN_ACCOUNT_EMAIL_RES below).

This script finds and (optionally) deletes those leftovers from the
databases involved, so cleanup is a single reviewable command instead of
hand-typed ad hoc SQL over SSH.

It talks to four independent SQLite databases directly via the stdlib sqlite3
module -- no repo imports, no ORM -- so it can run standalone on the
production box's bare python3, exactly like verify_live_deployment.py does:

  1. The gateway's metadata database (users, and schema_columns and
     dataset_profiles: the upload profiles), default /data/state/metadata.db,
     override with --metadata-db.
  2. The UASR service's database (uasr_drift_events, uasr_recovery_records,
     uasr_repair_watches, uasr_distribution_snapshots, uasr_batch_embeddings
     -- all keyed by a source_id column), default /data/state/uasr.db,
     override with --uasr-db.
  3. UASR's drift-state store (uasr_source_state, keyed by source_id),
     default /data/state/uasr_state.db, override with --uasr-state-db.
  4. The gateway's own database (gateway_dashboards), default
     /data/state/gateway.db, override with --gateway-db.

Usage
-----
    # Dry run (default) -- just prints what would be deleted, changes nothing:
    python3 scripts/cleanup_verify_resources.py

    # Actually delete the matched rows and files:
    python3 scripts/cleanup_verify_resources.py --confirm

    # Also delete the shared verify login (the live checks sign in with it, so
    # they fail until it is registered again):
    python3 scripts/cleanup_verify_resources.py --confirm --delete-login

    # Also delete the accounts the live-check loop registered:
    python3 scripts/cleanup_verify_resources.py --confirm --delete-run-accounts

    # Point at non-default DB locations (e.g. testing against a copy):
    python3 scripts/cleanup_verify_resources.py \\
        --metadata-db /path/to/metadata.db \\
        --uasr-db /path/to/uasr.db \\
        --uasr-state-db /path/to/uasr_state.db \\
        --gateway-db /path/to/gateway.db

Safety notes
------------
- The fixed login is an exact-string match, deleted only with --delete-login.
  The live-check loop's accounts are deleted only with --delete-run-accounts,
  and only when they match one of four patterns anchored at both ends, one
  per known shape. Before any account is deleted, every foreign key in
  metadata.db that points at users is looked up; if there is one, no account
  is deleted (that would orphan its rows) and the script says why. Today
  there is none: what an account created is keyed by its org_id in the other
  databases, not by a foreign key, and is not cascaded into -- only its
  verify_* rows go, through the rules here.
- source_id matching uses strict regexes mirroring Verifier.ns()'s actual
  f"verify_{uuid4().hex[:8]}_{label}" shape, with each label named ("uasr"
  in the UASR tables, "upload" in schema_columns) and at most one optional
  "<tenant>::" prefix. A bare substring match on "verify" is deliberately NOT
  used -- far too broad, could catch a real customer source.
- Dashboards match only the exact name verify_<8 hex>_dashboard. Their tiles
  live inline in gateway_dashboards.tiles_json, so a dashboard owns no rows
  elsewhere; a foreign key into gateway_dashboards would be handled like one
  into users.
- Every matched value is passed to SQL only as an exact-match `IN (...)`
  list of bound parameters -- never a wildcarded LIKE, never string-formatted
  SQL -- so there is no SQL injection surface.
- Row counts in both the dry-run report and the final summary come from
  COUNT(*) / cursor.rowcount against the real table, not from the number of
  distinct matched key values -- a single matched source_id can own many
  thousands of rows, and reporting "1" for that would badly understate impact.
- Defaults to a dry run. `--confirm` must be spelled exactly (argparse
  abbreviation is disabled) -- no partial/typo'd flag can trigger a delete.
- Any database file being absent or unreadable is handled gracefully: that
  one is skipped with a clear message, the others still run. So is a table
  an older schema does not have.
- The databases are deleted from independently and are NOT a single
  transaction (they are literally different files/processes). If one delete
  fails after the other already succeeded, the script reports exactly which
  side succeeded and which failed instead of leaving that ambiguous.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_METADATA_DB = "/data/state/metadata.db"
DEFAULT_UASR_DB = "/data/state/uasr.db"
DEFAULT_UASR_STATE_DB = "/data/state/uasr_state.db"
DEFAULT_GATEWAY_DB = "/data/state/gateway.db"
DEFAULT_UPLOADS_DIR = "/data/uploads"

# The defaults above are the CONTAINER's paths. Run on the host (the documented
# "run it on the box" procedure) they do not exist: the same files live in the
# aura-data Docker volume, so both databases were skipped and the script still
# reported "Nothing to do." (BUG-171). A default that is missing is therefore
# retried here; a path the operator passed explicitly is never substituted.
DOCKER_VOLUME_STATE_DIR = os.environ.get(
    "AURA_CLEANUP_FALLBACK_STATE_DIR", "/var/lib/docker/volumes/aura_aura-data/_data/state"
)

# The one manually-created test account. There is no auto-namespaced email
# convention in verify_live_deployment.py -- every check reuses one fixed
# STAGING_EMAIL login -- so this is an exact match, not a pattern.
MANUAL_VERIFY_EMAIL = "verify-uasr@example.com"

# Every pattern below is used with fullmatch(): `$` alone also matches before a
# trailing newline, and an exact rule must not.

# Accounts the live-check loop registers around verify_live_deployment.py: exactly
# these four shapes, deleted only with --delete-run-accounts (BUG-580).
RUN_ACCOUNT_EMAIL_RES = (
    re.compile(r"^verify_[0-9a-f]{8}@example\.com$"),
    re.compile(r"^verify-loop-[0-9]+@example\.com$"),
    re.compile(r"^loop-verify@example\.com$"),
    re.compile(r"^aura-live-verify-bot\+[0-9]+@dataaura\.local$"),
)

# An optional "<tenant>::" in front of a source id (BUG-580). uasr/tenancy.py stores
# an authenticated caller's source as f"{tenant}::{source_id}" and reads the tenant as
# everything before the first "::" -- any non-empty text without the separator -- and
# the upload indexer namespaces schema_columns ids the same way
# (shared/schema_indexer.py schema_source_id). At most one: a run's id is scoped once.
TENANT_PREFIX = r"(?:(?:(?!::).)+::)?"

# Mirrors Verifier.ns(): f"verify_{uuid4().hex[:8]}_{label}", each label named. The
# UASR self-heal check uses label="uasr"; the upload check's file is
# verify_<hex>_upload.csv, and schema_columns keys it by its stem. Case-sensitive.
VERIFY_SOURCE_ID_RE = re.compile("^" + TENANT_PREFIX + r"verify_[0-9a-f]{8}_uasr$")
VERIFY_UPLOAD_SOURCE_ID_RE = re.compile("^" + TENANT_PREFIX + r"verify_[0-9a-f]{8}_upload$")

# The dashboard_create check's dashboard. A dashboard's tenant is its workspace_id
# column, not part of its name, so there is no prefix here.
VERIFY_DASHBOARD_NAME_RE = re.compile(r"^verify_[0-9a-f]{8}_dashboard$")

# Files verify_live_deployment.py uploads: f"{v.ns(label)}.{ext}", i.e.
# "verify_<8 lowercase hex>_<label>.<ext>". They pile up in the staging account's
# upload directory (72 of them made every chat prompt overflow, BUG-444). Anchored
# on both ends; a customer's "verify_report.csv" does not match.
VERIFY_UPLOAD_RE = re.compile(r"^verify_[0-9a-f]{8}_[a-z0-9_]+\.(csv|tsv|xlsx|xls|json|parquet)$")

# Every UASR table with a source_id column (uasr/models.py). uasr_healing_metrics is
# keyed by domain, not by source, so it holds nothing of a verify source.
UASR_TABLES = (
    "uasr_drift_events",
    "uasr_recovery_records",
    "uasr_repair_watches",
    "uasr_distribution_snapshots",
    "uasr_batch_embeddings",
)
# SqliteStateStore (uasr/state_store.py): its own file, uasr_state.db (BUG-567).
UASR_STATE_TABLES = ("uasr_source_state",)

# The only (table, column) pairs a DELETE may name.
DELETABLE_KEYS = frozenset(
    [(t, "source_id") for t in UASR_TABLES + UASR_STATE_TABLES + ("schema_columns",)]
    + [("users", "email"), ("gateway_dashboards", "name"), ("dataset_profiles", "file_id")]
)


@dataclass(frozen=True)
class Match:
    table: str
    key_column: str
    value: str
    row_count: int


def is_verify_email(email: str) -> bool:
    return email == MANUAL_VERIFY_EMAIL


def is_run_account_email(email: str) -> bool:
    return email != MANUAL_VERIFY_EMAIL and any(r.fullmatch(email) for r in RUN_ACCOUNT_EMAIL_RES)


def is_verify_source_id(source_id: str) -> bool:
    return bool(VERIFY_SOURCE_ID_RE.fullmatch(source_id))


def is_verify_upload_source_id(source_id: str) -> bool:
    return bool(VERIFY_UPLOAD_SOURCE_ID_RE.fullmatch(source_id))


def is_verify_dashboard_name(name: str) -> bool:
    return bool(VERIFY_DASHBOARD_NAME_RE.fullmatch(name))


def _exists(path: Path) -> bool:
    """Like Path.exists(), but a path this user may not look into counts as absent:
    /var/lib/docker is root-only, so without sudo the fallback is simply unavailable."""
    try:
        return path.exists()
    except OSError:
        return False


def resolve_db_path(path: str, default: str, filename: str, fallback_dir: str | None = None) -> str:
    """The path to actually open: `path` itself unless it is the untouched
    default and missing, in which case the Docker-volume copy if that exists."""
    if path != default or _exists(Path(path)):
        return path
    fallback = Path(fallback_dir or DOCKER_VOLUME_STATE_DIR) / filename
    if _exists(fallback):
        print(f"[note] {path} not found; using the Docker volume copy at {fallback}")
        return str(fallback)
    return path


def open_db_readonly(path: str) -> sqlite3.Connection | None:
    """Open a SQLite file for reading, returning None (with a printed
    message) if the file is missing or cannot be opened, rather than
    raising -- either database may legitimately be unreachable on a given
    deployment (different volume mounts, a container not running)."""
    db_path = Path(path)
    if not db_path.exists():
        print(f"[skip] database not found: {path}")
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error as exc:
        print(f"[skip] could not open {path}: {exc}")
        return None


def find_key_matches(conn: sqlite3.Connection, tables: tuple[str, ...], key_column: str,
                     is_match) -> list[Match]:
    """One Match per distinct `key_column` value that `is_match` accepts, with its real
    row count. `tables` and `key_column` are always names fixed in this file, never data."""
    matches: list[Match] = []
    for table in tables:
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is None:
                print(f"[skip] table not found: {table}")
                continue
            distinct_values = [r[0] for r in conn.execute(f"SELECT DISTINCT {key_column} FROM {table}")]
        except sqlite3.Error as exc:
            print(f"[skip] could not query {table}: {exc}")
            continue

        for value in distinct_values:
            if not isinstance(value, str) or not is_match(value):
                continue
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {key_column} = ?", (value,)
            ).fetchone()
            matches.append(Match(table=table, key_column=key_column, value=value, row_count=count))
    return matches


def unless_referenced(conn: sqlite3.Connection, table: str, matches: list[Match]) -> list[Match]:
    """`matches`, or none of them when another table in this database holds a foreign
    key into `table`: deleting the rows would orphan the rows pointing at them, and
    those belong to whatever made them, so they are reported, never cascaded into."""
    if not matches:
        return matches
    try:
        refs = [
            f"{child}.{column}"
            for child, column in conn.execute(
                'SELECT m.name, f."from" FROM sqlite_master AS m, pragma_foreign_key_list(m.name) AS f '
                "WHERE m.type = 'table' AND lower(f.\"table\") = ?",
                (table,),
            )
        ]
    except sqlite3.Error as exc:
        refs = [f"(foreign keys could not be checked: {exc})"]
    if not refs:
        return matches
    print(f"[kept] {len(matches)} matched key(s) in {table}: referenced by {', '.join(refs)}, "
          "so deleting them would orphan those rows. Clean those up first.")
    return []


def find_metadata_matches(conn: sqlite3.Connection, delete_login: bool, delete_run_accounts: bool) -> list[Match]:
    def is_account(email: str) -> bool:
        return (delete_login and is_verify_email(email)) or (delete_run_accounts and is_run_account_email(email))

    users: list[Match] = []
    if delete_login or delete_run_accounts:
        users = unless_referenced(conn, "users", find_key_matches(conn, ("users",), "email", is_account))
    # The upload checks' profile rows are keyed by the uploaded file's own name, the
    # same exact shape as the files themselves (89 were left on production, BUG-580).
    profiles = find_key_matches(conn, ("dataset_profiles",), "file_id", lambda v: bool(VERIFY_UPLOAD_RE.fullmatch(v)))
    return users + find_key_matches(conn, ("schema_columns",), "source_id", is_verify_upload_source_id) + profiles


def find_uasr_matches(conn: sqlite3.Connection) -> list[Match]:
    return find_key_matches(conn, UASR_TABLES, "source_id", is_verify_source_id)


def find_uasr_state_matches(conn: sqlite3.Connection) -> list[Match]:
    return find_key_matches(conn, UASR_STATE_TABLES, "source_id", is_verify_source_id)


def find_gateway_matches(conn: sqlite3.Connection) -> list[Match]:
    dashboards = find_key_matches(conn, ("gateway_dashboards",), "name", is_verify_dashboard_name)
    return unless_referenced(conn, "gateway_dashboards", dashboards)


def scan_db(path: str, finder) -> tuple[bool, list[Match]]:
    """(opened, matches) for one database; one that cannot be opened is skipped."""
    conn = open_db_readonly(path)
    if conn is None:
        return False, []
    try:
        return True, finder(conn)
    finally:
        conn.close()


def find_upload_matches(uploads_dir: str) -> list[Path]:
    """verify_* files directly inside each tenant directory of the uploads root.

    Only regular files are matched -- never a symlink -- and only one level down,
    exactly where the gateway stores a tenant's uploads (<root>/<tenant>/<file>)."""
    root = Path(uploads_dir)
    found = []
    try:
        if not root.is_dir():
            print(f"[skip] uploads directory not found: {uploads_dir}")
            return []
        for tenant_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
            for f in sorted(tenant_dir.iterdir()):
                if f.is_file() and not f.is_symlink() and VERIFY_UPLOAD_RE.fullmatch(f.name):
                    found.append(f)
    except OSError as exc:
        print(f"[skip] could not read uploads directory {uploads_dir}: {exc}")
    return found


def print_report(title: str, matches: list[Match]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not matches:
        print("  (no matching rows)")
        return

    table_w = max(len("table"), *(len(m.table) for m in matches))
    col_w = max(len("key_column"), *(len(m.key_column) for m in matches))
    val_w = max(len("value"), *(len(m.value) for m in matches))
    count_w = max(len("row_count"), *(len(str(m.row_count)) for m in matches))

    header = f"  {'table':<{table_w}}  {'key_column':<{col_w}}  {'value':<{val_w}}  {'row_count':>{count_w}}"
    print(header)
    print(f"  {'-' * table_w}  {'-' * col_w}  {'-' * val_w}  {'-' * count_w}")
    for m in matches:
        print(f"  {m.table:<{table_w}}  {m.key_column:<{col_w}}  {m.value:<{val_w}}  {m.row_count:>{count_w}}")
    total_rows = sum(m.row_count for m in matches)
    print(f"  ({len(matches)} key(s), {total_rows} row(s) total)")


def delete_matches(db_path: str, matches: list[Match]) -> tuple[bool, int, str]:
    """Returns (ok, rows_deleted, message). Deletes table-by-table; if one
    table's DELETE fails partway through, reports exactly how many rows
    were removed before the failure rather than leaving that ambiguous."""
    by_key: dict[tuple[str, str], list[str]] = {}
    for m in matches:
        by_key.setdefault((m.table, m.key_column), []).append(m.value)
    if not by_key:
        return True, 0, "nothing to delete"

    total_deleted = 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            for (table, key_column), values in by_key.items():
                if (table, key_column) not in DELETABLE_KEYS:
                    # Defensive: never happens given how matches are built,
                    # but refuse to splice an unexpected name into SQL.
                    continue
                placeholders = ",".join("?" for _ in values)
                cur = conn.execute(f"DELETE FROM {table} WHERE {key_column} IN ({placeholders})", values)
                total_deleted += cur.rowcount
            conn.commit()
            return True, total_deleted, "OK"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, total_deleted, f"{exc} (partial: {total_deleted} row(s) deleted before failure)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and optionally delete verify_* test resources left behind by verify_live_deployment.py.",
        allow_abbrev=False,  # a typo'd/abbreviated flag must never silently become --confirm
    )
    parser.add_argument("--metadata-db", default=DEFAULT_METADATA_DB,
                         help=f"Path to the gateway metadata database (default: {DEFAULT_METADATA_DB})")
    parser.add_argument("--uasr-db", default=DEFAULT_UASR_DB,
                         help=f"Path to the UASR service database (default: {DEFAULT_UASR_DB})")
    parser.add_argument("--uasr-state-db", default=DEFAULT_UASR_STATE_DB,
                         help=f"Path to UASR's drift-state store (default: {DEFAULT_UASR_STATE_DB})")
    parser.add_argument("--gateway-db", default=DEFAULT_GATEWAY_DB,
                         help=f"Path to the gateway database holding dashboards (default: {DEFAULT_GATEWAY_DB})")
    parser.add_argument("--uploads-dir", default=DEFAULT_UPLOADS_DIR,
                         help=f"Uploads root, one directory per tenant (default: {DEFAULT_UPLOADS_DIR})")
    parser.add_argument("--confirm", action="store_true",
                         help="Actually delete the matched rows. Without this flag, only reports what would be deleted.")
    parser.add_argument("--delete-login", action="store_true",
                         help=f"Also delete the shared {MANUAL_VERIFY_EMAIL} login. Off by default: "
                              "verify_live_deployment.py signs in with it.")
    parser.add_argument("--delete-run-accounts", action="store_true",
                         help="Also delete the accounts the live-check loop registered (four exact shapes; "
                              f"never {MANUAL_VERIFY_EMAIL}, which needs --delete-login).")
    args = parser.parse_args()

    metadata_path = resolve_db_path(args.metadata_db, DEFAULT_METADATA_DB, "metadata.db")
    uasr_path = resolve_db_path(args.uasr_db, DEFAULT_UASR_DB, "uasr.db")
    uasr_state_path = resolve_db_path(args.uasr_state_db, DEFAULT_UASR_STATE_DB, "uasr_state.db")
    gateway_path = resolve_db_path(args.gateway_db, DEFAULT_GATEWAY_DB, "gateway.db")
    uploads_path = resolve_db_path(
        args.uploads_dir, DEFAULT_UPLOADS_DIR, "uploads", fallback_dir=str(Path(DOCKER_VOLUME_STATE_DIR).parent),
    )

    print("cleanup_verify_resources.py")
    print(f"  metadata db: {metadata_path}")
    print(f"  uasr db:     {uasr_path}")
    print(f"  uasr state:  {uasr_state_path}")
    print(f"  gateway db:  {gateway_path}")
    print(f"  uploads:     {uploads_path}")
    print(f"  login:       {'DELETE (--delete-login given)' if args.delete_login else f'kept ({MANUAL_VERIFY_EMAIL})'}")
    print(f"  accounts:    {'DELETE (--delete-run-accounts given)' if args.delete_run_accounts else 'kept (pass --delete-run-accounts to delete)'}")
    print(f"  mode:        {'DELETE (--confirm given)' if args.confirm else 'DRY RUN (pass --confirm to delete)'}")

    # The live checks sign in with the fixed login; deleting it, or the loop's accounts, is opt-in.
    metadata_opened, metadata_matches = scan_db(
        metadata_path, lambda conn: find_metadata_matches(conn, args.delete_login, args.delete_run_accounts),
    )

    upload_matches = find_upload_matches(uploads_path)

    uasr_opened, uasr_matches = scan_db(uasr_path, find_uasr_matches)
    uasr_state_opened, uasr_state_matches = scan_db(uasr_state_path, find_uasr_state_matches)
    gateway_opened, gateway_matches = scan_db(gateway_path, find_gateway_matches)

    if not (metadata_opened or uasr_opened or uasr_state_opened or gateway_opened) and not _exists(Path(uploads_path)):
        # Reporting "Nothing to do." here would read as a clean box when in fact
        # no database was examined at all.
        print()
        print("NOTHING WAS CHECKED: no database could be opened, so leftover test rows may still be there.")
        print(
            "Point the script at the real files with --metadata-db, --uasr-db, --uasr-state-db and --gateway-db "
            f"(on the box they are in {DOCKER_VOLUME_STATE_DIR}, and may need sudo)."
        )
        return 2

    print_report("Gateway metadata.db -- users to delete", [m for m in metadata_matches if m.table == "users"])
    print_report("Gateway metadata.db -- upload schema_columns to delete",
                 [m for m in metadata_matches if m.table == "schema_columns"])
    print_report("Gateway metadata.db -- upload dataset_profiles to delete",
                 [m for m in metadata_matches if m.table == "dataset_profiles"])
    print_report("UASR uasr.db -- source_ids to delete", uasr_matches)
    print_report("UASR uasr_state.db -- source_ids to delete", uasr_state_matches)
    print_report("Gateway gateway.db -- dashboards to delete", gateway_matches)
    print("\nUploaded files to delete")
    print("------------------------")
    for f in upload_matches:
        print(f"  {f}")
    print(f"  ({len(upload_matches)} file(s))" if upload_matches else "  (no matching files)")

    per_db = (
        ("metadata.db", metadata_path, metadata_matches),
        ("uasr.db", uasr_path, uasr_matches),
        ("uasr_state.db", uasr_state_path, uasr_state_matches),
        ("gateway.db", gateway_path, gateway_matches),
    )
    total_rows = sum(m.row_count for _, _, matches in per_db for m in matches)
    if total_rows == 0 and not upload_matches:
        print("\nNothing to do.")
        return 0

    if not args.confirm:
        print(f"\nDry run only: {total_rows} row(s) and {len(upload_matches)} file(s) would be deleted. "
              "Re-run with --confirm to delete them.")
        return 0

    exit_code = 0

    print()
    for label, path, matches in per_db:
        if not matches:
            print(f"{label}: nothing matched, no delete attempted.")
            continue
        ok, deleted, msg = delete_matches(path, matches)
        status = "OK" if ok else "FAILED"
        print(f"{label}: {status} -- {deleted} row(s) deleted. {msg}")
        if not ok:
            exit_code = 1

    removed = 0
    for f in upload_matches:
        try:
            f.unlink()
            removed += 1
        except OSError as exc:
            print(f"uploads: FAILED to delete {f}: {exc}")
            exit_code = 1
    print(f"uploads: {removed} of {len(upload_matches)} file(s) deleted.")

    if exit_code != 0:
        print("\nOne or more deletes FAILED -- see above for exactly which database/rows were "
              "affected before re-running. Re-running is safe: an already-deleted row simply "
              "won't match a second time.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
