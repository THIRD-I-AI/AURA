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
# CRON (daily 03:15, log to the box):
#   15 3 * * * cd /home/ec2-user/AURA/deploy/aws-free-tier && ./backup.sh >> backup.log 2>&1
#
set -euo pipefail

BACKUP_ROOT="${AURA_BACKUP_ROOT:-$(pwd)/backups}"
RETENTION_DAYS="${AURA_BACKUP_RETENTION_DAYS:-7}"
SERVICE="api_gateway"
STAGE="/data/_backup_stage"
SQLITE_DBS="gateway.db ledger.db metadata.db uasr.db"
FILE_DIRS="keys uploads audit artifacts uasr_parquet"

die() { echo "FAILED: $*" >&2; exit 1; }

compose() { docker compose "$@"; }

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
DEST="$BACKUP_ROOT/$STAMP"
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
fi

echo "== pruning backups older than ${RETENTION_DAYS}d =="
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} + \
  || echo "warn: prune failed (backup itself succeeded)"

echo "OK  $DEST  ($(du -sh "$DEST" | cut -f1))"
