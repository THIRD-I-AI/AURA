#!/usr/bin/env bash
#
# AURA — back up the aura-data volume off the box.
#
# WHY THIS EXISTS: every piece of durable state this deployment has lives in one
# unreplicated docker volume — the four SQLite databases (gateway, ledger,
# metadata, uasr), the ED25519 signing keys, every uploaded dataset, and the
# tamper-evident audit hash chain. Losing that volume loses all of it with no
# recovery path. `docker compose down -v`, a bad `docker volume rm`, or an EBS
# failure are all one command or one bad day away.
#
# WHY NOT `cp` THE .db FILES: copying a live SQLite file can capture a torn
# write and yield a database that opens fine and is subtly corrupt. This uses
# the sqlite3 module's online backup API (conn.backup), which is safe against a
# concurrently-written database. Python is used rather than the sqlite3 CLI
# because the runtime image ships Python and does not ship the CLI.
#
# RESTORE:
#   1. docker compose down
#   2. ./backup.sh --restore 20260821T075500Z
#   3. docker compose up -d
#   Restore refuses to run while containers are up, because writing a database
#   file under a live process is how you get the corruption you were avoiding.
#
# RUN AS ec2-user, NEVER AS ROOT (BUG-581): a root run leaves backups/ owned by
# root, and the ec2-user cron then fails every night. The script refuses uid 0.
# For a restore emergency where only root will do, set AURA_BACKUP_ALLOW_ROOT=1,
# then chown the backups back to ec2-user.
#
# STATUS: every backup run records its result in /data/state/backup_status.json
# on the volume. The gateway's /health reports it as checks.backup (ok, stale,
# failed, missing); scripts/verify_live_deployment.py's backup_fresh fails
# unless it is ok.
#
# CRON (daily 03:15 as ec2-user, log to the box, copy off the box to S3):
#   15 3 * * * cd /opt/aura/deploy/aws-free-tier && AURA_S3_BACKUP_BUCKET=my-bucket ./backup.sh >> backup.log 2>&1
#
set -euo pipefail

BACKUP_ROOT="${AURA_BACKUP_ROOT:-$(pwd)/backups}"
RETENTION_DAYS="${AURA_BACKUP_RETENTION_DAYS:-7}"
SERVICE="api_gateway"
STAGE="/data/_backup_stage"
# uasr_state.db is the durable drift-state store (BUG-567). It holds every
# source's learned baseline; losing it makes the next batch the new "normal".
SQLITE_DBS="gateway.db ledger.db metadata.db uasr.db uasr_state.db"
FILE_DIRS="keys uploads audit artifacts uasr_parquet"
# Read by the gateway's /health (api_gateway/main.py BACKUP_STATUS_FILE).
STATUS_FILE="/data/state/backup_status.json"
# Set only once a backup starts: a refused root run or restore is not a failed
# backup, and must not overwrite the last real result.
DEST=""

die() {
  echo "FAILED: $*" >&2
  if [ -n "$DEST" ] && [ -n "$(compose ps -q "$SERVICE" 2>/dev/null)" ]; then
    write_status failed "$*" || echo "warn: could not record the failure in $STATUS_FILE" >&2
  fi
  exit 1
}

compose() { docker compose "$@"; }

# Written through the container, so the file lands on the volume owned by the
# user the gateway runs as. No secrets: status, finish time, stamp, size, offsite
# flag and the error text.
write_status() {  # ok|failed [error]
  compose exec -T "$SERVICE" python3 -c '
import json, os, sys
from datetime import datetime, timezone
path, status, stamp, size, offsite, error = sys.argv[1:]
record = {"status": status, "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "stamp": stamp, "size": size or None, "offsite": offsite == "true", "error": error or None}
with open(path + ".tmp", "w") as f:
    json.dump(record, f)
os.replace(path + ".tmp", path)  # /health never reads a half-written record
' "$STATUS_FILE" "$1" "$STAMP" "${SIZE:-}" "$OFFSITE" "${2:-}"
}

# BUG-581: one root run on 2026-09-26 left backups/ root-owned, and the ec2-user
# cron then failed every night for 58 days.
if [ "$(id -u)" = 0 ] && [ "${AURA_BACKUP_ALLOW_ROOT:-}" != 1 ]; then
  die "refusing to run as root: it leaves $BACKUP_ROOT root-owned and the ec2-user cron can no longer write there. Run as ec2-user, or set AURA_BACKUP_ALLOW_ROOT=1 for a restore emergency."
fi

# ── restore ─────────────────────────────────────────────────────────
if [ "${1:-}" = "--restore" ]; then
  STAMP="${2:-}"
  [ -n "$STAMP" ] || die "--restore needs a timestamp; see $BACKUP_ROOT"
  SRC="$BACKUP_ROOT/$STAMP"
  [ -d "$SRC" ] || die "no backup at $SRC"
  if [ -n "$(compose ps -q 2>/dev/null)" ]; then
    die "containers are running — 'docker compose down' first (restoring under a live writer corrupts the DB)"
  fi
  echo "Restoring $STAMP ..."
  compose run --rm --no-deps -T -v "$SRC:/restore:ro" "$SERVICE" \
    sh -c 'set -e; mkdir -p /data/state; cp /restore/state/*.db /data/state/ 2>/dev/null || true;
           [ -f /restore/state/uasr.duckdb ] && cp /restore/state/uasr.duckdb /data/state/ || true;
           tar xzf /restore/files.tar.gz -C /data' \
    || die "restore failed"
  echo "Restored $STAMP. Start with: docker compose up -d"
  exit 0
fi

# ── backup ──────────────────────────────────────────────────────────
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OFFSITE=false
DEST="$BACKUP_ROOT/$STAMP"
# Name the owner, not just mkdir's bare "Permission denied" (BUG-581).
if [ -d "$BACKUP_ROOT" ] && [ ! -w "$BACKUP_ROOT" ]; then
  die "$BACKUP_ROOT is owned by $(stat -c %U "$BACKUP_ROOT") and not writable by $(id -un). Fix: sudo chown -R $(id -un) $BACKUP_ROOT"
fi
mkdir -p "$DEST" || die "cannot create $DEST"

CID="$(compose ps -q "$SERVICE" 2>/dev/null || true)"
[ -n "$CID" ] || die "$SERVICE is not running; cannot reach the volume"

echo "== staging inside the container =="
compose exec -T "$SERVICE" sh -c "rm -rf $STAGE && mkdir -p $STAGE/state" \
  || die "could not create staging dir"

for DB in $SQLITE_DBS; do
  compose exec -T "$SERVICE" python3 -c "
import sqlite3, sys, os
src = '/data/state/$DB'
if not os.path.exists(src):
    print('  skip $DB (absent)'); sys.exit(0)
s = sqlite3.connect('file:%s?mode=ro' % src, uri=True)
d = sqlite3.connect('$STAGE/state/$DB')
s.backup(d)          # online backup API — safe against concurrent writers
d.close(); s.close()
print('  ok   $DB')
" || die "sqlite backup of $DB failed"
done

# DuckDB has no online-backup API. It is copied as a plain file, so it is only
# crash-consistent. Acceptable here and nowhere else: uasr.duckdb holds drift
# baselines and batch snapshots, which the detector relearns from live data.
# Nothing irreplaceable lives in it — unlike ledger.db above.
compose exec -T "$SERVICE" sh -c \
  "[ -f /data/state/uasr.duckdb ] && cp /data/state/uasr.duckdb $STAGE/state/ || true" \
  || die "duckdb copy failed"

echo "== archiving keys, uploads and audit artifacts =="
compose exec -T "$SERVICE" sh -c \
  "cd /data && tar czf $STAGE/files.tar.gz \$(for d in $FILE_DIRS; do [ -e \"\$d\" ] && echo \"\$d\"; done)" \
  || die "tar of file directories failed"

echo "== copying off the volume =="
docker cp "$CID:$STAGE/state" "$DEST/state" || die "docker cp of state failed"
docker cp "$CID:$STAGE/files.tar.gz" "$DEST/files.tar.gz" || die "docker cp of files.tar.gz failed"
compose exec -T "$SERVICE" sh -c "rm -rf $STAGE" || echo "warn: could not clean $STAGE"

# A backup you never verified is a guess. Confirm each DB actually opens.
for DB in $SQLITE_DBS; do
  [ -f "$DEST/state/$DB" ] || continue
  python3 -c "
import sqlite3, sys
c = sqlite3.connect('$DEST/state/$DB')
if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
    sys.exit('integrity_check failed for $DB')
c.close()
" || die "restored copy of $DB failed integrity_check"
done

if [ -n "${AURA_S3_BACKUP_BUCKET:-}" ]; then
  echo "== syncing to s3://$AURA_S3_BACKUP_BUCKET =="
  aws s3 sync "$DEST" "s3://$AURA_S3_BACKUP_BUCKET/$STAMP" || die "s3 sync failed"
  OFFSITE=true
fi

echo "== pruning backups older than ${RETENTION_DAYS}d =="
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} + \
  || echo "warn: prune failed (backup itself succeeded)"

SIZE="$(du -sh "$DEST" | cut -f1)"
write_status ok || echo "warn: backup succeeded but $STATUS_FILE was not updated; /health still shows the previous run" >&2
echo "OK  $DEST  ($SIZE)"
