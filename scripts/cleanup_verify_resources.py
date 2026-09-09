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
"verify_a1b2c3d4_uasr". The script never auto-creates login accounts --
every run uses the one fixed STAGING_EMAIL/STAGING_PASSWORD login -- so the
only leftover *account* is whatever a human manually registered for that
purpose. As of 2026-09, that is the single exact address
"verify-uasr@example.com".

This script finds and (optionally) deletes those leftovers from the two
databases involved, so cleanup is a single reviewable command instead of
hand-typed ad hoc SQL over SSH.

It talks to two independent SQLite databases directly via the stdlib sqlite3
module -- no repo imports, no ORM -- so it can run standalone on the
production box's bare python3, exactly like verify_live_deployment.py does:

  1. The gateway's metadata database (users table), default
     /data/state/metadata.db, override with --metadata-db.
  2. The UASR service's database (uasr_drift_events, uasr_recovery_records,
     uasr_distribution_snapshots, uasr_batch_embeddings -- all keyed by a
     source_id column), default /data/state/uasr.db, override with
     --uasr-db.

Usage
-----
    # Dry run (default) -- just prints what would be deleted, changes nothing:
    python3 scripts/cleanup_verify_resources.py

    # Actually delete the matched rows:
    python3 scripts/cleanup_verify_resources.py --confirm

    # Point at non-default DB locations (e.g. testing against a copy):
    python3 scripts/cleanup_verify_resources.py \\
        --metadata-db /path/to/metadata.db \\
        --uasr-db /path/to/uasr.db

Safety notes
------------
- Email matching is an exact-string match against the one known manual test
  account -- verify_live_deployment.py has no per-run namespaced email
  convention, so a regex there would only invent false matches.
- UASR source_id matching uses a strict regex mirroring Verifier.ns()'s
  actual f"verify_{uuid4().hex[:8]}_uasr" shape. A bare substring match on
  "verify" is deliberately NOT used -- far too broad, could catch a real
  customer source.
- Every matched value is passed to SQL only as an exact-match `IN (...)`
  list of bound parameters -- never a wildcarded LIKE, never string-formatted
  SQL -- so there is no SQL injection surface.
- Row counts in both the dry-run report and the final summary come from
  COUNT(*) / cursor.rowcount against the real table, not from the number of
  distinct matched key values -- a single matched source_id can own many
  thousands of rows, and reporting "1" for that would badly understate impact.
- Defaults to a dry run. `--confirm` must be spelled exactly (argparse
  abbreviation is disabled) -- no partial/typo'd flag can trigger a delete.
- Either database file being absent or unreadable is handled gracefully:
  that half is skipped with a clear message, the other half still runs.
- The two databases are deleted from independently and are NOT a single
  transaction (they are literally different files/processes). If one delete
  fails after the other already succeeded, the script reports exactly which
  side succeeded and which failed instead of leaving that ambiguous.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_METADATA_DB = "/data/state/metadata.db"
DEFAULT_UASR_DB = "/data/state/uasr.db"

# The one manually-created test account. There is no auto-namespaced email
# convention in verify_live_deployment.py -- every check reuses one fixed
# STAGING_EMAIL login -- so this is an exact match, not a pattern.
MANUAL_VERIFY_EMAIL = "verify-uasr@example.com"

# Mirrors Verifier.ns(): f"verify_{uuid4().hex[:8]}_{label}". The UASR
# self-heal check always uses label="uasr", so the full shape is
# "verify_<8 lowercase hex>_uasr" -- anchored on both ends, case-sensitive.
VERIFY_SOURCE_ID_RE = re.compile(r"^verify_[0-9a-f]{8}_uasr$")

UASR_TABLES = (
    "uasr_drift_events",
    "uasr_recovery_records",
    "uasr_distribution_snapshots",
    "uasr_batch_embeddings",
)


@dataclass(frozen=True)
class Match:
    table: str
    key_column: str
    value: str
    row_count: int


def is_verify_email(email: str) -> bool:
    return email == MANUAL_VERIFY_EMAIL


def is_verify_source_id(source_id: str) -> bool:
    return bool(VERIFY_SOURCE_ID_RE.match(source_id))


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


def find_metadata_matches(conn: sqlite3.Connection) -> list[Match]:
    try:
        rows = conn.execute("SELECT email FROM users WHERE email = ?", (MANUAL_VERIFY_EMAIL,)).fetchall()
    except sqlite3.Error as exc:
        print(f"[skip] could not query users table: {exc}")
        return []
    # A given email is a unique login, so this is always 0 or 1 row -- but
    # compute the real count rather than assuming, for the same reason the
    # UASR side does: never let a printed count be a guess.
    if not rows:
        return []
    return [Match(table="users", key_column="email", value=MANUAL_VERIFY_EMAIL, row_count=len(rows))]


def find_uasr_matches(conn: sqlite3.Connection) -> list[Match]:
    matches: list[Match] = []
    for table in UASR_TABLES:
        try:
            distinct_ids = [r[0] for r in conn.execute(f"SELECT DISTINCT source_id FROM {table}")]
        except sqlite3.Error as exc:
            print(f"[skip] could not query {table}: {exc}")
            continue

        for source_id in distinct_ids:
            if source_id is None or not is_verify_source_id(source_id):
                continue
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_id = ?", (source_id,)
            ).fetchone()
            matches.append(Match(table=table, key_column="source_id", value=source_id, row_count=count))
    return matches


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


def delete_metadata_matches(db_path: str, matches: list[Match]) -> tuple[bool, int, str]:
    """Returns (ok, rows_deleted, message)."""
    if not matches:
        return True, 0, "nothing to delete"
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute("DELETE FROM users WHERE email = ?", (MANUAL_VERIFY_EMAIL,))
            conn.commit()
            return True, cur.rowcount, "OK"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, 0, str(exc)


def delete_uasr_matches(db_path: str, matches: list[Match]) -> tuple[bool, int, str]:
    """Returns (ok, rows_deleted, message). Deletes table-by-table; if one
    table's DELETE fails partway through, reports exactly how many rows
    were removed before the failure rather than leaving that ambiguous."""
    by_table: dict[str, list[str]] = {}
    for m in matches:
        by_table.setdefault(m.table, []).append(m.value)
    if not by_table:
        return True, 0, "nothing to delete"

    total_deleted = 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            for table, source_ids in by_table.items():
                if table not in UASR_TABLES:
                    # Defensive: never happens given how matches are built,
                    # but refuse to splice an unexpected table name into SQL.
                    continue
                placeholders = ",".join("?" for _ in source_ids)
                cur = conn.execute(f"DELETE FROM {table} WHERE source_id IN ({placeholders})", source_ids)
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
    parser.add_argument("--confirm", action="store_true",
                         help="Actually delete the matched rows. Without this flag, only reports what would be deleted.")
    args = parser.parse_args()

    print("cleanup_verify_resources.py")
    print(f"  metadata db: {args.metadata_db}")
    print(f"  uasr db:     {args.uasr_db}")
    print(f"  mode:        {'DELETE (--confirm given)' if args.confirm else 'DRY RUN (pass --confirm to delete)'}")

    metadata_matches: list[Match] = []
    metadata_conn = open_db_readonly(args.metadata_db)
    if metadata_conn is not None:
        try:
            metadata_matches = find_metadata_matches(metadata_conn)
        finally:
            metadata_conn.close()

    uasr_matches: list[Match] = []
    uasr_conn = open_db_readonly(args.uasr_db)
    if uasr_conn is not None:
        try:
            uasr_matches = find_uasr_matches(uasr_conn)
        finally:
            uasr_conn.close()

    print_report("Gateway metadata.db -- users to delete", metadata_matches)
    print_report("UASR uasr.db -- source_ids to delete", uasr_matches)

    total_rows = sum(m.row_count for m in metadata_matches) + sum(m.row_count for m in uasr_matches)
    if total_rows == 0:
        print("\nNothing to do.")
        return 0

    if not args.confirm:
        print(f"\nDry run only: {total_rows} row(s) would be deleted. Re-run with --confirm to delete them.")
        return 0

    exit_code = 0

    if metadata_matches:
        ok, deleted, msg = delete_metadata_matches(args.metadata_db, metadata_matches)
        status = "OK" if ok else "FAILED"
        print(f"\nmetadata.db: {status} -- {deleted} row(s) deleted. {msg}")
        if not ok:
            exit_code = 1
    else:
        print("\nmetadata.db: nothing matched, no delete attempted.")

    if uasr_matches:
        ok, deleted, msg = delete_uasr_matches(args.uasr_db, uasr_matches)
        status = "OK" if ok else "FAILED"
        print(f"uasr.db: {status} -- {deleted} row(s) deleted. {msg}")
        if not ok:
            exit_code = 1
    else:
        print("uasr.db: nothing matched, no delete attempted.")

    if exit_code != 0:
        print("\nOne or more deletes FAILED -- see above for exactly which database/rows were "
              "affected before re-running. Re-running is safe: an already-deleted row simply "
              "won't match a second time.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
