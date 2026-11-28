#!/bin/bash
# AURA restore drill: the part that runs ON the throwaway drill instance, as root, via SSM.
# restore-drill.sh embeds this file and restore-drill-checks.py in one SSM command and starts it
# with AURA_DRILL_REGION, AURA_DRILL_BUCKET, AURA_DRILL_STAMP and AURA_ENV_SSM_PARAM set.
#
# The restored app runs on a Docker network with no route out (`internal: true`), so even a job
# that fires cannot reach customer databases, webhooks, streams or LLM providers. COMPOSE_FILE is
# exported before the first compose call and forwarded through every sudo, so the override applies
# to everything, `backup.sh --restore` included. Nothing is restored until that network has proved
# internal, and the app is stopped at once if it then turns out to have a route out.
#
# Output is the drill report: readable steps, then a closing block of machine-read lines
#   DRILL_CHECK <name> <PASS|FAIL|SKIP> <detail>   every REQUIRED check must PASS
#   DRILL_NOTE <text>                              reported, never gating
#   DRILL_VERDICT <PASS|FAIL>, then DRILL_END      the block is complete
# restore-drill.sh judges that block rather than trusting this script's exit status alone.
# Nothing here prints a secret: the production .env goes to a file, and only counts are reported.

# Each of these must report PASS. restore-drill.sh holds the same list; a test keeps them equal.
REQUIRED=(network_internal restore health db_integrity ledger_chains live_checks)
APP_USER=ec2-user
DRILL_DIR=/root/drill                         # where restore-drill.sh unpacked the checks
CHECKS_IN_APP=/tmp/restore-drill-checks.py    # where they are copied inside the app container

declare -A CHECK_STATUS CHECK_DETAIL
RECORDED=()
NOTES=()
CURRENT_STEP=startup
VERDICT=""
SCRATCH=""

step() { CURRENT_STEP=$*; printf '\n== %s ==\n' "$*"; }

record() {  # name PASS|FAIL|SKIP detail
  [ -n "${CHECK_STATUS[$1]:-}" ] || RECORDED+=("$1")
  CHECK_STATUS[$1]=$2
  CHECK_DETAIL[$1]=$3
  printf '  [%s] %s: %s\n' "$2" "$1" "$3"
}

note() { NOTES+=("$*"); printf '  note: %s\n' "$*"; }

emit_check() {
  local name=$1 where=$2
  if [ -n "${CHECK_STATUS[$name]:-}" ]; then
    printf 'DRILL_CHECK %s %s %s\n' "$name" "${CHECK_STATUS[$name]}" "${CHECK_DETAIL[$name]}"
    [ "${CHECK_STATUS[$name]}" = PASS ] || VERDICT=FAIL
  else
    printf 'DRILL_CHECK %s SKIP not reached (stopped during: %s)\n' "$name" "$where"
    VERDICT=FAIL
  fi
}

finish() {  # print the verdict block, once. A check that never ran counts as failed.
  [ -z "$VERDICT" ] || return 0
  local where=$CURRENT_STEP name i
  VERDICT=PASS
  step verdict
  for name in "${REQUIRED[@]}"; do emit_check "$name" "$where"; done
  for name in ${RECORDED[@]+"${RECORDED[@]}"}; do  # anything else the checks reported gates too
    case " ${REQUIRED[*]} " in *" $name "*) ;; *) emit_check "$name" "$where" ;; esac
  done
  for ((i = 0; i < ${#NOTES[@]}; i++)); do printf 'DRILL_NOTE %s\n' "${NOTES[$i]}"; done
  printf 'DRILL_VERDICT %s\n' "$VERDICT"
  echo DRILL_END
}

conclude() {
  finish
  if [ "$VERDICT" = PASS ]; then exit 0; else exit 1; fi
}

abort() { echo "ABORT: $*"; conclude; }

on_exit() {
  if [ -z "$VERDICT" ]; then  # the script died before it could conclude
    echo "drill script stopped unexpectedly during: $CURRENT_STEP"
    finish
    [ -z "$SCRATCH" ] || rm -rf "$SCRATCH"
    exit 1
  fi
  [ -z "$SCRATCH" ] || rm -rf "$SCRATCH"
}

absorb() {  # fold the DRILL_CHECK / DRILL_NOTE lines restore-drill-checks.py printed into this report
  local line name status detail
  while IFS= read -r line; do
    case $line in
      "DRILL_CHECK "*) read -r name status detail <<<"${line#DRILL_CHECK }"; record "$name" "$status" "$detail" ;;
      "DRILL_NOTE "*) note "${line#DRILL_NOTE }" ;;
    esac
  done <"$1"
}

env_value() {  # KEY -> its value from .env, read without sourcing the file and never printed
  sed -n "s/^$1=//p" .env | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

main() {
  set -uo pipefail
  exec 2>&1
  trap on_exit EXIT
  SCRATCH=$(mktemp -d)

  local region=${AURA_DRILL_REGION:-} bucket=${AURA_DRILL_BUCKET:-} stamp=${AURA_DRILL_STAMP:-}
  local env_param=${AURA_ENV_SSM_PARAM:-/aura/prod/env}
  [ -n "$region" ] && [ -n "$bucket" ] || abort "AURA_DRILL_REGION and AURA_DRILL_BUCKET must be set"
  case $stamp in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) abort "AURA_DRILL_STAMP must be a backup timestamp such as 20261126T031500Z" ;;
  esac

  step "wait for the instance setup"
  local i
  for i in $(seq 1 120); do
    if [ -e /var/log/drill-setup.done ] || [ -e /var/log/drill-setup.failed ]; then break; fi
    sleep 5
  done
  if [ ! -e /var/log/drill-setup.done ]; then
    tail -n 20 /var/log/drill-setup.log 2>/dev/null
    abort "the instance setup (docker, compose, swap) did not complete"
  fi

  step "fetch the code and the production env (the env is never printed)"
  rm -rf /opt/aura
  git clone -q --depth 1 https://github.com/THIRD-I-AI/AURA.git /opt/aura || abort "git clone failed"
  cd /opt/aura/deploy/aws-free-tier || abort "the clone has no deploy/aws-free-tier"
  git -C /opt/aura log --oneline -1
  (umask 077 && aws ssm get-parameter --region "$region" --name "$env_param" --with-decryption \
    --query Parameter.Value --output text >.env) || abort "could not read $env_param from SSM"
  [ -s .env ] || abort "$env_param is empty"
  echo "env keys: $(grep -c '^[A-Z]' .env)"
  chown -R "$APP_USER:$APP_USER" /opt/aura

  step "isolation override: every compose call from here on runs through it"
  cat >docker-compose.drill.yml <<'YAML'
networks:
  default:
    internal: true
services:
  api_gateway:
    environment:
      UASR_MAPEK_ENABLED: "false"
  uasr_service:
    environment:
      UASR_MAPEK_ENABLED: "false"
YAML
  chown "$APP_USER:$APP_USER" docker-compose.drill.yml
  export COMPOSE_FILE=docker-compose.yml:docker-compose.drill.yml
  # Defined after the export on purpose: sudo drops the environment, so each call forwards
  # COMPOSE_FILE itself, and no compose call can come before the export.
  as_app_user() { sudo -u "$APP_USER" env "COMPOSE_FILE=$COMPOSE_FILE" "$@"; }
  dc() { as_app_user docker compose "$@"; }
  echo "compose $(dc version --short)"

  step "fetch the offsite backup $stamp"
  as_app_user mkdir -p "backups/$stamp"
  as_app_user aws s3 sync "s3://$bucket/$stamp" "backups/$stamp" --region "$region" --only-show-errors \
    || abort "could not download s3://$bucket/$stamp"
  [ -n "$(find "backups/$stamp" -type f | head -n 1)" ] || abort "s3://$bucket/$stamp is empty"
  (cd "backups/$stamp" && find . -type f -exec ls -l {} + | awk '{print $5, $9}')
  # What the backup holds, so a restore that silently produced nothing cannot pass the integrity check.
  local expect_dbs
  expect_dbs=$(cd "backups/$stamp/state" 2>/dev/null && ls -1 ./*.db 2>/dev/null | sed 's|^\./||' | tr '\n' ' ')

  step "pull images (the docker daemon still has internet; the app will not)"
  local ghcr_user ghcr_token
  ghcr_user=$(env_value GHCR_USER)
  ghcr_token=$(env_value GHCR_TOKEN)
  if [ -n "$ghcr_user" ] && [ -n "$ghcr_token" ]; then
    if printf '%s' "$ghcr_token" | as_app_user docker login ghcr.io -u "$ghcr_user" --password-stdin >/dev/null 2>&1; then
      echo "ghcr.io login ok"
    else
      echo "ghcr.io login failed; pulling anonymously"
    fi
  fi
  unset ghcr_token
  dc pull -q api_gateway uasr_service redis || abort "image pull failed"

  step "isolation precheck: build the project network from the override, abort unless it is internal"
  dc create redis >"$SCRATCH/create.out" 2>&1 || tail -n 20 "$SCRATCH/create.out"
  local internal
  internal=$(docker network inspect aura_default --format '{{.Internal}}' 2>/dev/null || true)
  echo "aura_default internal=${internal:-absent}"
  if [ "$internal" != true ]; then
    record network_internal FAIL "aura_default is not an internal network (internal=${internal:-absent}); nothing was restored or started"
    abort "the drill network is not internal"
  fi

  step "restore with the documented procedure: ./backup.sh --restore $stamp"
  if as_app_user ./backup.sh --restore "$stamp"; then
    record restore PASS "./backup.sh --restore $stamp"
  else
    record restore FAIL "./backup.sh --restore $stamp failed"
    abort "the restore failed"
  fi

  step "start the restored app and prove it is isolated"
  dc up -d api_gateway uasr_service || abort "could not start the restored app"
  local gw cid net networks_internal=1
  gw=$(dc ps -q api_gateway)
  [ -n "$gw" ] || abort "api_gateway is not running"
  for cid in $(dc ps -q); do
    for net in $(docker inspect "$cid" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'); do
      [ "$(docker network inspect "$net" --format '{{.Internal}}')" = true ] || networks_internal=0
    done
  done
  docker cp "$DRILL_DIR/restore-drill-checks.py" "$gw:$CHECKS_IN_APP" || abort "could not copy the checks into the app"
  if [ "$networks_internal" = 1 ] && docker exec "$gw" python3 "$CHECKS_IN_APP" isolation; then
    record network_internal PASS "aura_default is internal, every app container is on internal networks only, and the app has no route out"
  else
    dc down >/dev/null 2>&1
    record network_internal FAIL "isolation not proven (every container on internal networks: $networks_internal); the app was stopped"
    abort "isolation not proven"
  fi

  step "wait for the restored app to report healthy"
  local health="" healthy=0
  for i in $(seq 1 60); do
    if health=$(docker exec "$gw" python3 "$CHECKS_IN_APP" health 2>/dev/null); then healthy=1; break; fi
    sleep 5
  done
  echo "health: ${health:-no response}"
  if [ "$healthy" = 1 ]; then
    record health PASS "$health"
  else
    record health FAIL "${health:-no answer from /health within 5 minutes}"
  fi

  step "data checks inside the restored app (counts only, never values)"
  docker exec -e "DRILL_EXPECT_DBS=$expect_dbs" "$gw" python3 "$CHECKS_IN_APP" data >"$SCRATCH/data.out" 2>&1
  grep -v '^DRILL_' "$SCRATCH/data.out"
  absorb "$SCRATCH/data.out"

  step "live checks against the restored app"
  if [ "$healthy" != 1 ]; then
    record live_checks SKIP "not run: the app never became healthy"
  elif ! docker exec "$gw" python3 "$CHECKS_IN_APP" register; then
    record live_checks FAIL "could not register the throwaway account the verifier logs in with"
  else
    docker cp /opt/aura/scripts/verify_live_deployment.py "$gw:/tmp/verify.py" || abort "could not copy the verifier into the app"
    timeout 1500 docker exec "$gw" sh -c \
      'STAGING_EMAIL=$(sed -n 1p /tmp/drill_creds) STAGING_PASSWORD=$(sed -n 2p /tmp/drill_creds) python3 /tmp/verify.py --url http://localhost:8000 --out /tmp/drill_report.md' \
      >"$SCRATCH/live.out" 2>&1
    docker exec "$gw" rm -f /tmp/drill_creds
    grep -E '^ *\[(PASS|FAIL|SKIP)\] |passed,' "$SCRATCH/live.out" || tail -n 30 "$SCRATCH/live.out"
    python3 "$DRILL_DIR/restore-drill-checks.py" live <"$SCRATCH/live.out" >"$SCRATCH/live.verdict" 2>&1
    grep -v '^DRILL_' "$SCRATCH/live.verdict"
    absorb "$SCRATCH/live.verdict"
  fi

  conclude
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
