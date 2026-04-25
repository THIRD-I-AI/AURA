#!/usr/bin/env bash
# AURA backend launcher — POSIX mirror of start_all.ps1.
# Boots all 9 microservices under uvicorn --reload, each in the background.
# Logs to /tmp/aura-<service>.log; PIDs written to /tmp/aura-<service>.pid.
#
# Usage:
#   ./start_all.sh         # start
#   ./start_all.sh --kill  # stop services started by this script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$SCRIPT_DIR"
ENV_FILE="$ROOT/.env"
LOG_DIR="${AURA_LOG_DIR:-/tmp}"

# ── Resolve python ──────────────────────────────────────────────────
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
  echo "[env] Using .venv python: $PYTHON"
else
  PYTHON="${PYTHON:-python3}"
  echo "[env] No .venv found, using $PYTHON"
fi

# ── Load .env (KEY=VALUE lines, skipping comments) ──────────────────
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE")
  set +a
  echo "[env] Loaded .env"
fi

export PYTHONPATH="$BACKEND"
export PYTHONUNBUFFERED=1

# Service definitions: "Name|Port|Module"
SERVICES=(
  "API-Gateway|8000|api_gateway.main:app"
  "Code-Generation|8001|code_generation_service.main:code_gen_app"
  "Connectors-Vault|8002|connectors.main:app"
  "Exec-Sandbox|8003|execution_sandbox.main:execution_app"
  "Scheduler|8004|scheduler_service.main:scheduler_app"
  "Insights|8005|insights.main:app"
  "Orchestration|8006|orchestration_service.main:app"
  "Metadata-Store|8007|metadata_store.main:metadata_app"
  "UASR-Service|8009|uasr.service:app"
)

if [[ "${1:-}" == "--kill" ]]; then
  echo "[cleanup] Stopping AURA services..."
  for entry in "${SERVICES[@]}"; do
    name="${entry%%|*}"
    pidfile="$LOG_DIR/aura-$name.pid"
    if [[ -f "$pidfile" ]]; then
      pid="$(cat "$pidfile")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" || true
        echo "  [-] $name (pid $pid)"
      fi
      rm -f "$pidfile"
    fi
  done
  exit 0
fi

echo "============================================================"
echo "  AURA Backend  -  Starting ${#SERVICES[@]} Services"
echo "  DB: ${DB_HOST:-?}:${DB_PORT:-?}/${DB_NAME:-?}"
echo "  Python: $PYTHON"
echo "  Logs:   $LOG_DIR/aura-<service>.log"
echo "============================================================"

cd "$BACKEND"
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name port module <<< "$entry"
  log="$LOG_DIR/aura-$name.log"
  pidfile="$LOG_DIR/aura-$name.pid"
  nohup "$PYTHON" -m uvicorn "$module" --host 0.0.0.0 --port "$port" --reload \
    > "$log" 2>&1 &
  echo $! > "$pidfile"
  echo "  [+] $name -> http://localhost:$port  (pid $(cat "$pidfile"))"
done

echo "============================================================"
echo "  All services launching in background."
echo "  Wait ~5s then run:  $PYTHON test_operability.py"
echo "  Stop with:          ./start_all.sh --kill"
echo "============================================================"
