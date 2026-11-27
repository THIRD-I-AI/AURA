#!/usr/bin/env bash
#
# AURA — keep the disaster-recovery copy of .env current.
#
# WHY THIS EXISTS: .env holds the secrets the data volume cannot be used without
# (AURA_CREDENTIAL_ENCRYPTION_KEY decrypts every saved connection password,
# SECRET_KEY signs logins), and it is not part of the volume backup. Since
# BUG-581 a copy lives in SSM as a SecureString. A copy that is not refreshed when
# .env changes would, after a lost box, restore old secrets and leave the restored
# databases' saved passwords undecryptable.
#
# redeploy.sh runs this on every deploy (every 15 minutes from cron). It writes to
# SSM only when the file's hash differs from the last copy it pushed, so the
# parameter's version history records each real change and nothing else.
# The box's role may only PutParameter on this one name; it cannot read it back.
#
# USAGE: ./env-backup.sh        (from cron via redeploy.sh, or by hand after editing .env)
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PARAM="${AURA_ENV_SSM_PARAM:-/aura/prod/env}"
REGION="${AURA_ENV_SSM_REGION:-us-east-1}"
STAMP_FILE=".env.ssm.sha256"

if [ ! -f .env ]; then
    echo "env-backup: no .env here; nothing to copy"
    exit 0
fi
command -v aws >/dev/null 2>&1 || { echo "env-backup: aws CLI not found; $PARAM not refreshed" >&2; exit 1; }

hash="$(sha256sum .env | cut -d' ' -f1)"
if [ "$hash" = "$(cat "$STAMP_FILE" 2>/dev/null)" ]; then
    exit 0
fi

if aws ssm put-parameter --region "$REGION" --name "$PARAM" --type SecureString \
        --overwrite --value file://.env >/dev/null; then
    printf '%s\n' "$hash" > "$STAMP_FILE"
    echo "env-backup: .env changed; refreshed its SSM copy ($PARAM)"
else
    echo "env-backup: .env changed but $PARAM could not be updated; the disaster-recovery copy is stale" >&2
    exit 1
fi
