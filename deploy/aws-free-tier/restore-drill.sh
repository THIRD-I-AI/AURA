#!/usr/bin/env bash
#
# AURA — prove the offsite backup restores, repeatably, without touching production.
#
# WHY THIS EXISTS: a backup nobody has restored is a guess. The manual drill of 2026-11-26 restored
# the newest S3 backup onto a fresh box and found BUG-593 (a fresh volume could not be restored).
# This is that drill as one command: it restores the backup onto a THROWAWAY instance whose restored
# app has no route out, checks the result, and always deletes everything it created.
#
# RUN IT from an operator workstation with an admin AWS session (`aws sts get-caller-identity`
# works). It needs aws, base64, gzip, awk, sed, grep and tr on PATH:
#   ./restore-drill.sh --prod-instance i-0123456789abcdef0
#   ./restore-drill.sh --prod-instance i-0123456789abcdef0 --bucket my-backups --stamp 20261126T031500Z
# See --help, and README.md ("Restore drill") for what it proves, costs and guarantees.
#
# WHAT IT DOES: reads the production instance's subnet and VPC (read-only; nothing is ever sent to
# it); creates a temporary IAM role + instance profile (read the backup bucket and one SSM
# parameter) and a security group with no inbound rules; launches a t3.small from
# restore-drill-userdata.sh; waits for SSM; runs restore-drill-onbox.sh on it; saves the report to
# ./restore-drill-<stamp>.log; then ALWAYS terminates the instance, waits for it to die, and deletes
# the profile, role and security group (trap ... EXIT, also on Ctrl-C).
#
# EXIT STATUS: 0 the drill passed; 1 it failed or could not complete; 2 bad usage; 3 teardown left
# resources behind (they are named in the output; delete them by hand).

# Each of these must report PASS in the on-box report. restore-drill-onbox.sh holds the same list;
# a test keeps them equal.
REQUIRED_CHECKS="network_internal restore health db_integrity ledger_chains live_checks"
INSTANCE_TYPE=t3.small
VOLUME_GB=30
AMI_PARAM=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
PURPOSE=temporary-restore-drill
SSM_ONLINE_WAIT=900   # seconds for the new instance to register with SSM (its setup runs meanwhile)
DRILL_WAIT=3900       # seconds for the on-box drill; its SSM executionTimeout is 3600

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT=fail           # set to pass only after the report has been judged
LEAKED=""             # resources teardown could not remove

usage() {
  cat <<'EOF'
Usage: restore-drill.sh --prod-instance ID [--region REGION] [--bucket BUCKET] [--stamp STAMP]

Restores an offsite backup onto a throwaway, network-isolated instance, checks it, and deletes
everything it created. The report is saved to ./restore-drill-<stamp>.log

  --prod-instance ID  the production instance (or AURA_DRILL_PROD_INSTANCE). Only described, never
                      touched: the drill reuses its subnet and VPC.
  --region REGION     default: $AWS_REGION, then $AWS_DEFAULT_REGION, then us-east-1
  --bucket BUCKET     default: $AURA_S3_BACKUP_BUCKET (what backup.sh uses), then aura-backups-<account>
  --stamp STAMP       backup to restore, e.g. 20261126T031500Z (or AURA_DRILL_STAMP). Default: the newest.

AURA_ENV_SSM_PARAM names the SecureString holding the production .env (default /aura/prod/env).

Exit status: 0 passed; 1 failed or could not complete; 2 bad usage; 3 teardown left resources behind.
EOF
}

usage_error() { echo "restore-drill: $*" >&2; echo "Run with --help for usage." >&2; exit 2; }
die() { echo "restore-drill: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

valid() { printf '%s' "$2" | grep -Eq "$1"; }  # regex, value

# aws.exe under Git Bash ends every line with CRLF, and a trailing \r inside $(...) turns an instance
# id into a different, invalid string. Strip it once here instead of at every call site.
aws() { command aws --region "$REGION" "$@" | tr -d '\r'; }

# Defense in depth: the drill prints counts, never secrets, but a tool's error text is not ours to
# vouch for. Masks the key shapes that scripts/hooks/block_secrets.py blocks from commits.
redact() {
  sed -E \
    -e 's/gsk_[A-Za-z0-9]{20,}/<redacted>/g' \
    -e 's/(^|[^A-Za-z0-9_])sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}/\1<redacted>/g' \
    -e 's/AIza[0-9A-Za-z_-]{30,}/<redacted>/g' \
    -e 's/gh[pousr]_[A-Za-z0-9]{30,}/<redacted>/g' \
    -e 's/AKIA[0-9A-Z]{16}/<redacted>/g' \
    -e 's/xox[baprs]-[A-Za-z0-9-]{10,}/<redacted>/g'
}

# ── what the drill instance may do ──────────────────────────────────────────

TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# Read-only: list and get on the backup bucket, and read the one SSM parameter holding the production
# .env. No write of any kind and no other bucket. The SSM agent's own permissions come from the
# AmazonSSMManagedInstanceCore managed policy, which is broader than this inline policy, so the role
# is short-lived rather than minimal: the drill deletes it on every exit.
role_policy() {  # bucket account env-param
  printf '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::%s"},{"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::%s/*"},{"Effect":"Allow","Action":"ssm:GetParameter","Resource":"arn:aws:ssm:%s:%s:parameter%s"}]}' \
    "$1" "$1" "$REGION" "$2" "$3"
}

# ── the SSM command ─────────────────────────────────────────────────────────

# One SSM command that unpacks both on-box files and runs the drill. gzip keeps it far below the
# 32 KB command-line limit of a Windows workstation and below SSM's request limit. Every value
# interpolated here was validated against a strict character set, so none can break out of the JSON
# or the shell command it lands in.
build_command_parameters() {  # region bucket stamp env-param
  local onbox checks
  onbox=$(gzip -9nc "$HERE/restore-drill-onbox.sh" | base64 | tr -d '\n\r')
  checks=$(gzip -9nc "$HERE/restore-drill-checks.py" | base64 | tr -d '\n\r')
  printf '{"executionTimeout":["3600"],"commands":["mkdir -p /root/drill","echo %s | base64 -d | gunzip > /root/drill/restore-drill-onbox.sh","echo %s | base64 -d | gunzip > /root/drill/restore-drill-checks.py","AURA_DRILL_REGION=%s AURA_DRILL_BUCKET=%s AURA_DRILL_STAMP=%s AURA_ENV_SSM_PARAM=%s bash /root/drill/restore-drill-onbox.sh"]}' \
    "$onbox" "$checks" "$1" "$2" "$3" "$4"
}

# Decides pass/fail from the saved report alone. Every required check needs a PASS line, no
# DRILL_CHECK may say FAIL, and the closing DRILL_END must be there: a truncated report or a dead
# instance has none. A check that is missing counts as failed. Returns 0 only on a full pass.
judge_report() {  # report-file ssm-status
  local file=$1 ssm_status=$2 rc=0 name line status detail
  for name in $REQUIRED_CHECKS; do
    line=$(grep "^DRILL_CHECK $name " "$file" | tail -n 1)
    status=$(printf '%s\n' "$line" | awk '{print $3}')
    detail=${line#"DRILL_CHECK $name $status "}
    printf '  %-8s %-16s %s\n' "${status:-MISSING}" "$name" "$detail"
    [ "$status" = PASS ] || rc=1
  done
  if grep -q '^DRILL_CHECK [^ ]* FAIL' "$file"; then rc=1; fi
  if ! grep -q '^DRILL_END$' "$file"; then echo "  the report has no DRILL_END, so the drill did not finish"; rc=1; fi
  if [ "$ssm_status" != Success ]; then echo "  the on-box command ended as $ssm_status, not Success"; rc=1; fi
  return $rc
}

# Polls until the SSM command leaves its running states and prints the final status.
wait_for_command() {  # command-id instance-id
  local deadline=$(( $(date +%s) + DRILL_WAIT )) status last=""
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$1" --instance-id "$2" --query Status --output text 2>/dev/null) || status=Pending
    if [ "$status" != "$last" ]; then echo "  drill: $status" >&2; last=$status; fi
    case $status in Pending|InProgress|Delayed|Cancelling) ;; *) break ;; esac
    if [ "$(date +%s)" -ge "$deadline" ]; then status=TimedOut; break; fi
    sleep 15
  done
  printf '%s\n' "$status"
}

# ── teardown ────────────────────────────────────────────────────────────────

# Removes one resource. "Already gone" is success (a run can die before it creates something); any
# other failure is a leak the operator must clear by hand.
teardown_step() {  # description command...
  local what=$1 out
  shift
  if out=$("$@" 2>&1); then echo "  removed $what"; return 0; fi
  case $out in
    *NoSuchEntity*|*NotFound*) echo "  $what: already gone"; return 0 ;;
  esac
  echo "  FAILED to remove $what: $out" >&2
  LEAKED="$LEAKED $what;"
  return 1
}

# Resources are found by their unique name, not by variables, so a run that died between "created"
# and "remembered" still cleans up after itself.
teardown() {
  step "teardown (always runs)"
  local ids sg attempt out
  ids=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
    --query 'Reservations[].Instances[].InstanceId' --output text) || ids="?"
  if [ "$ids" = "?" ]; then
    echo "  FAILED to look up the instance named $NAME" >&2
    LEAKED="$LEAKED instance named $NAME (look-up failed);"
  elif [ -n "$ids" ] && [ "$ids" != None ]; then
    echo "  terminating $ids"
    if aws ec2 terminate-instances --instance-ids $ids >/dev/null && aws ec2 wait instance-terminated --instance-ids $ids; then
      echo "  terminated $ids"
    else
      LEAKED="$LEAKED instance $ids (still running? terminate it);"
    fi
  else
    echo "  no instance to terminate"
  fi

  teardown_step "instance profile $NAME (role detach)" aws iam remove-role-from-instance-profile --instance-profile-name "$NAME" --role-name "$NAME"
  teardown_step "instance profile $NAME" aws iam delete-instance-profile --instance-profile-name "$NAME"
  teardown_step "role policy of $NAME" aws iam delete-role-policy --role-name "$NAME" --policy-name drill-access
  teardown_step "AmazonSSMManagedInstanceCore on $NAME" aws iam detach-role-policy --role-name "$NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  teardown_step "role $NAME" aws iam delete-role --role-name "$NAME"

  sg=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$NAME" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text) || sg="?"
  if [ "$sg" = "?" ]; then
    echo "  FAILED to look up the security group named $NAME" >&2
    LEAKED="$LEAKED security group named $NAME (look-up failed);"
  elif [ "$sg" != None ] && [ -n "$sg" ]; then
    # The instance's network interface can outlive "terminated" by a few seconds.
    for attempt in $(seq 1 24); do
      if out=$(aws ec2 delete-security-group --group-id "$sg" 2>&1); then echo "  removed security group $sg"; sg=""; break; fi
      case $out in
        *DependencyViolation*) sleep 5 ;;
        *NotFound*) echo "  security group $sg: already gone"; sg=""; break ;;
        *) break ;;
      esac
    done
    [ -z "$sg" ] || { echo "  FAILED to remove security group $sg: $out" >&2; LEAKED="$LEAKED security group $sg;"; }
  fi

  if [ -n "${REPORT:-}" ] && [ -e "$REPORT" ]; then
    if [ -n "$LEAKED" ]; then echo "# teardown INCOMPLETE:$LEAKED" >>"$REPORT"; else echo "# teardown complete: instance terminated; profile, role and security group deleted" >>"$REPORT"; fi
  fi
}

cleanup() {
  trap '' INT TERM HUP   # a second Ctrl-C must not abandon a half-finished teardown
  teardown
  if [ -n "$LEAKED" ]; then
    echo "restore-drill: TEARDOWN INCOMPLETE. Delete by hand:$LEAKED" >&2
    exit 3
  fi
  if [ "$RESULT" = pass ]; then
    echo "restore-drill: PASSED. Report: ${REPORT:-none}"
    exit 0
  fi
  echo "restore-drill: FAILED. Report: ${REPORT:-none (the drill did not get far enough to write one)}" >&2
  exit 1
}

# ── main ────────────────────────────────────────────────────────────────────

main() {
  set -uo pipefail
  # Git Bash rewrites arguments that look like POSIX paths (resolve:ssm:/aws/..., Ebs=...=/dev/xvda)
  # before handing them to aws.exe; a no-op everywhere else. A pager would block any command whose
  # output is not captured.
  export MSYS_NO_PATHCONV=1 AWS_PAGER=""

  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
  local prod_instance="${AURA_DRILL_PROD_INSTANCE:-}" stamp="${AURA_DRILL_STAMP:-}"
  local bucket="${AURA_S3_BACKUP_BUCKET:-}" env_param="${AURA_ENV_SSM_PARAM:-/aura/prod/env}"
  while [ $# -gt 0 ]; do
    case $1 in
      -h|--help) usage; exit 0 ;;
      --prod-instance|--region|--bucket|--stamp)
        [ $# -ge 2 ] || usage_error "$1 needs a value"
        case $1 in
          --prod-instance) prod_instance=$2 ;;
          --region) REGION=$2 ;;
          --bucket) bucket=$2 ;;
          --stamp) stamp=$2 ;;
        esac
        shift 2 ;;
      *) usage_error "unknown argument: $1" ;;
    esac
  done

  # These values end up inside JSON and shell commands on the instance: accept only a strict charset.
  valid '^[a-z]{2}(-[a-z]+)+-[0-9]$' "$REGION" || usage_error "--region must look like us-east-1"
  valid '^i-[0-9a-f]{8,17}$' "$prod_instance" || usage_error "--prod-instance is required and must look like i-0123456789abcdef0"
  [ -z "$bucket" ] || valid '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$' "$bucket" || usage_error "--bucket is not a valid S3 bucket name"
  [ -z "$stamp" ] || valid '^[0-9]{8}T[0-9]{6}Z$' "$stamp" || usage_error "--stamp must look like 20261126T031500Z"
  valid '^/[A-Za-z0-9_./-]+$' "$env_param" || usage_error "AURA_ENV_SSM_PARAM must be an SSM parameter path like /aura/prod/env"
  local tool
  for tool in aws base64 gzip tr awk sed grep sort; do
    type -P "$tool" >/dev/null || usage_error "$tool is required but was not found on PATH"
  done

  step "preflight (read-only)"
  local account net
  account=$(aws sts get-caller-identity --query Account --output text) || die "no usable AWS session: aws sts get-caller-identity failed"
  # The one and only use of the production instance: where to put the drill instance.
  net=$(aws ec2 describe-instances --instance-ids "$prod_instance" \
    --query 'Reservations[0].Instances[0].[SubnetId,VpcId]' --output text) || die "cannot describe $prod_instance in $REGION"
  read -r SUBNET_ID VPC_ID <<<"$net"
  valid '^subnet-[0-9a-f]+$' "$SUBNET_ID" && valid '^vpc-[0-9a-f]+$' "$VPC_ID" \
    || die "$prod_instance has no subnet and VPC in $REGION (is it a running instance there?)"
  [ -n "$bucket" ] || bucket="aura-backups-$account"
  if [ -z "$stamp" ]; then
    stamp=$(aws s3 ls "s3://$bucket/" | awk '$1 == "PRE" {print $2}' | tr -d '/' | grep -E '^[0-9]{8}T[0-9]{6}Z$' | sort | tail -n 1) || true
    [ -n "$stamp" ] || die "no backup found in s3://$bucket/ (right bucket, region and account?)"
  else
    aws s3 ls "s3://$bucket/$stamp/" >/dev/null || die "no backup $stamp in s3://$bucket/"
  fi

  local run_id
  run_id=$(date -u +%Y%m%dT%H%M%SZ)
  NAME="aura-restore-drill-$run_id"
  REPORT="restore-drill-$stamp.log"
  [ ! -e "$REPORT" ] || REPORT="restore-drill-$stamp-$run_id.log"
  echo "restore drill $run_id"
  echo "  backup    s3://$bucket/$stamp"
  echo "  network   $VPC_ID / $SUBNET_ID, found from $prod_instance (read-only)"
  echo "  instance  $INSTANCE_TYPE, ${VOLUME_GB} GB encrypted gp3, no inbound rules, terminated on exit"
  echo "  report    $REPORT"

  # From here on something exists in the account that must not outlive this run.
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  step "temporary IAM role and instance profile $NAME"
  aws iam create-role --role-name "$NAME" --assume-role-policy-document "$TRUST_POLICY" \
    --description "Temporary AURA restore drill; deleted by restore-drill.sh" \
    --tags "Key=purpose,Value=$PURPOSE" >/dev/null || die "could not create the IAM role"
  aws iam attach-role-policy --role-name "$NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore \
    || die "could not attach AmazonSSMManagedInstanceCore"
  aws iam put-role-policy --role-name "$NAME" --policy-name drill-access \
    --policy-document "$(role_policy "$bucket" "$account" "$env_param")" || die "could not attach the access policy"
  aws iam create-instance-profile --instance-profile-name "$NAME" >/dev/null || die "could not create the instance profile"
  aws iam add-role-to-instance-profile --instance-profile-name "$NAME" --role-name "$NAME" \
    || die "could not add the role to the instance profile"

  step "security group $NAME (no inbound rules) in $VPC_ID"
  local sg_id
  sg_id=$(aws ec2 create-security-group --group-name "$NAME" --vpc-id "$VPC_ID" \
    --description "AURA restore drill (temporary, no inbound)" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$NAME},{Key=purpose,Value=$PURPOSE}]" \
    --query GroupId --output text) || die "could not create the security group"

  step "launch the drill instance"
  local iid="" attempt err
  err=$(mktemp)
  # A new instance profile is not visible to EC2 for a few seconds.
  for attempt in $(seq 1 12); do
    if iid=$(aws ec2 run-instances \
        --image-id "resolve:ssm:$AMI_PARAM" --instance-type "$INSTANCE_TYPE" --count 1 \
        --subnet-id "$SUBNET_ID" --security-group-ids "$sg_id" --associate-public-ip-address \
        --iam-instance-profile "Name=$NAME" \
        --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
        --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
        --instance-initiated-shutdown-behavior terminate \
        --user-data "$(cat "$HERE/restore-drill-userdata.sh")" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=purpose,Value=$PURPOSE}]" \
                             "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=purpose,Value=$PURPOSE}]" \
        --query 'Instances[0].InstanceId' --output text 2>"$err"); then
      break
    fi
    grep -q "Invalid IAM Instance Profile" "$err" || { cat "$err" >&2; rm -f "$err"; die "could not launch the drill instance"; }
    echo "  waiting for the instance profile to propagate (attempt $attempt)"
    sleep 10
  done
  rm -f "$err"
  valid '^i-[0-9a-f]+$' "$iid" || die "the drill instance was not launched"
  echo "  $iid"

  step "wait for $iid to register with SSM"
  aws ec2 wait instance-running --instance-ids "$iid" || die "$iid did not reach running"
  local waited=0 ping=""
  while [ "$waited" -lt "$SSM_ONLINE_WAIT" ]; do
    ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$iid" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null) || ping=""
    [ "$ping" = Online ] && break
    sleep 10
    waited=$((waited + 10))
  done
  [ "$ping" = Online ] || die "$iid never registered with SSM within ${SSM_ONLINE_WAIT}s"

  step "run the drill on $iid"
  local params cmd_id ssm_status output
  params=$(build_command_parameters "$REGION" "$bucket" "$stamp" "$env_param")
  # The only instance ever sent a command is the drill instance, by the id run-instances returned.
  cmd_id=$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --comment "AURA restore drill $run_id" --parameters "$params" \
    --query Command.CommandId --output text) || die "could not send the drill to $iid"
  echo "  ssm command $cmd_id"
  ssm_status=$(wait_for_command "$cmd_id" "$iid")

  step "collect the report"
  output=$(aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" \
    --query StandardOutputContent --output text) || output=""
  [ "$output" != None ] || output=""
  {
    echo "# AURA restore drill $run_id: s3://$bucket/$stamp, $REGION, instance $iid, ssm command $cmd_id ($ssm_status)"
    printf '%s\n' "$output" | redact
  } >"$REPORT"
  cat "$REPORT"

  step "result"
  if judge_report "$REPORT" "$ssm_status"; then RESULT=pass; fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
