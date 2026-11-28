"""The restore drill (deploy/aws-free-tier/restore-drill*.{sh,py}) is the only proof the offsite backup
restores, and it runs against a real AWS account, where a slip costs money or touches production.

Nothing here calls AWS. Three layers:
  * static assertions pin the properties that must never regress: the isolation override is exported before
    the first compose call, the restore aborts unless the network is internal, teardown runs from an EXIT trap,
    the dead-man switch is armed first, production is only ever described;
  * bash tests source the scripts (sourcing runs nothing) and drive their functions, and one whole simulated
    drill, against a fake `aws` function. A fail-closed `aws` shim sits first on PATH, so anything that
    escaped the fake would fail the test instead of reaching AWS;
  * unit tests for restore-drill-checks.py, against real SQLite, the real ledger and credentials modules and a
    real local HTTP server.
"""
from __future__ import annotations

import base64
import gzip
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import update

from shared import audit_ledger as L
from shared import credentials

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/aws-free-tier"
ORCHESTRATOR = DEPLOY / "restore-drill.sh"
ONBOX = DEPLOY / "restore-drill-onbox.sh"
USERDATA = DEPLOY / "restore-drill-userdata.sh"
CHECKS = DEPLOY / "restore-drill-checks.py"
VERIFIER = ROOT / "scripts/verify_live_deployment.py"
SHELL_SCRIPTS = [ORCHESTRATOR, ONBOX, USERDATA]

PROD_INSTANCE = "i-0123456789abcdef0"
DRILL_INSTANCE = "i-0fedcba987654321"  # what the fake aws returns from run-instances
STAMP = "20261126T031500Z"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """The file without comment-only lines, so a comment can never satisfy an assertion about code."""
    return "\n".join(line for line in _text(path).splitlines() if not line.lstrip().startswith("#"))


def _joined(path: Path) -> str:
    """Code with line continuations joined and whitespace collapsed, so a multi-line command reads as one."""
    return re.sub(r"\s+", " ", _code(path).replace("\\\n", " "))


def _load_checks():
    spec = importlib.util.spec_from_file_location("restore_drill_checks", CHECKS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks = _load_checks()


def _required_in_onbox() -> list[str]:
    return re.search(r"^REQUIRED=\((.*?)\)", _text(ONBOX), re.M).group(1).split()


REQUIRED = _required_in_onbox()


# ── static: the properties that must never regress ──────────────────────────


def test_onbox_exports_compose_file_before_any_compose_call():
    code = _code(ONBOX)
    export_at = code.index("export COMPOSE_FILE=")
    assert "docker-compose.drill.yml" in code[export_at:export_at + 120]
    assert export_at < code.index("docker compose"), "a compose call precedes the COMPOSE_FILE export"
    assert export_at < code.index("backup.sh --restore"), "the restore precedes the COMPOSE_FILE export"
    # Every compose call goes through dc(), and every sudo forwards COMPOSE_FILE itself: sudo drops the
    # environment, so an export alone would not reach backup.sh or compose when they run as the app user.
    assert len(re.findall(r"docker compose", code)) == 1
    assert re.search(r'as_app_user\(\) \{ sudo -u "\$APP_USER" env "COMPOSE_FILE=\$COMPOSE_FILE" "\$@"; \}', code)
    assert "as_app_user ./backup.sh --restore" in code


def test_onbox_aborts_unless_the_drill_network_is_internal():
    code = _code(ONBOX)
    inspect_at = code.index("docker network inspect aura_default --format '{{.Internal}}'")
    gate = re.search(r'if \[ "\$internal" != true \]; then(.*?)\n  fi\n', code[inspect_at:], re.S)
    assert gate, "no gate on the network's Internal flag"
    assert "network_internal FAIL" in gate.group(1) and "abort " in gate.group(1)
    assert inspect_at + gate.end() < code.index("backup.sh --restore"), "the gate must come before the restore"
    # The running containers are inspected and probed too, and the app is stopped if it can get out.
    assert re.search(r'python3 "\$CHECKS_IN_APP" isolation', code) and "dc down" in code


def test_override_makes_the_default_network_internal_and_stops_the_self_healing_loop():
    heredoc = re.search(r"<<'YAML'\n(.*?)\nYAML\n", _text(ONBOX), re.S).group(1)
    override = yaml.safe_load(heredoc)
    assert override["networks"]["default"]["internal"] is True
    for service in ("api_gateway", "uasr_service"):
        assert override["services"][service]["environment"]["UASR_MAPEK_ENABLED"] == "false"


def test_the_drill_network_and_services_exist_in_the_compose_file():
    compose = yaml.safe_load(_text(DEPLOY / "docker-compose.yml"))
    # `docker network inspect aura_default` is only right while the compose project is named aura.
    assert compose["name"] == "aura"
    heredoc = re.search(r"<<'YAML'\n(.*?)\nYAML\n", _text(ONBOX), re.S).group(1)
    assert set(yaml.safe_load(heredoc)["services"]) <= set(compose["services"])
    assert "redis" in compose["services"]  # created on its own to build the network before any app container


def test_the_orchestrator_tears_everything_down_from_an_exit_trap_in_order():
    code = _code(ORCHESTRATOR)
    assert re.search(r"^\s*trap cleanup EXIT$", code, re.M)
    assert "trap 'exit 130' INT" in code and "trap 'exit 143' TERM" in code and "trap 'exit 129' HUP" in code
    assert "trap '' INT TERM HUP" in code, "a second Ctrl-C must not abandon the teardown"
    assert code.index("trap cleanup EXIT") < code.index("aws iam create-role"), "the trap must precede every create"
    body = re.search(r"\nteardown\(\) \{(.*?)\n\}\n", code, re.S).group(1)
    order = [
        "aws ec2 terminate-instances", "aws ec2 wait instance-terminated", "aws iam remove-role-from-instance-profile",
        "aws iam delete-instance-profile", "aws iam delete-role-policy", "aws iam detach-role-policy",
        "aws iam delete-role --role-name", "aws ec2 delete-security-group",
    ]
    positions = [body.index(step) for step in order]
    assert positions == sorted(positions), "terminate, wait for termination, then profile, role, security group"
    assert "Name=tag:Name" in body, "teardown must find its resources by name, not by remembered variables"


def test_user_data_arms_the_dead_man_switch_first_and_the_instance_terminates_on_shutdown():
    lines = [line for line in _code(USERDATA).splitlines() if line.strip()]
    assert lines[0].startswith("shutdown -h +150"), "the dead-man switch must be the first thing user-data does"
    assert "--instance-initiated-shutdown-behavior terminate" in _joined(ORCHESTRATOR)
    assert 'cat "$HERE/restore-drill-userdata.sh"' in _code(ORCHESTRATOR)
    waits = {name: int(value) for name, value in re.findall(r"^(SSM_ONLINE_WAIT|DRILL_WAIT)=(\d+)", _code(ORCHESTRATOR), re.M)}
    assert sum(waits.values()) + 600 < 150 * 60, "the orchestrator must give up well before the dead-man switch fires"


def test_the_instance_is_launched_locked_down():
    code = _joined(ORCHESTRATOR)
    launch = code[code.index("aws ec2 run-instances"):code.index("--query 'Instances[0].InstanceId'")]
    for flag in ("HttpTokens=required", "Encrypted=true", "DeleteOnTermination=true", "Key=purpose,Value=$PURPOSE"):
        assert flag in launch, flag
    assert "--key-name" not in code, "no SSH key: the only way in is SSM"
    assert "authorize-security-group-ingress" not in code, "the security group must have no inbound rules"
    assert re.search(r"^PURPOSE=temporary-restore-drill$", _code(ORCHESTRATOR), re.M)
    assert re.search(r"^INSTANCE_TYPE=t3\.small$", _code(ORCHESTRATOR), re.M)


def test_the_orchestrator_never_sends_anything_to_the_production_instance():
    code = _joined(ORCHESTRATOR)
    # Every instance an aws call can name: the production one appears once, in the read-only look-up.
    assert {t.rstrip(";") for t in re.findall(r"--instance-ids (\S+)", code)} == {'"$prod_instance"', '"$iid"', "$ids"}
    assert {t.rstrip(";") for t in re.findall(r"--instance-id (\S+)", code)} <= {'"$iid"', '"$2"'}
    needle = '--instance-ids "$prod_instance"'
    assert code.count(needle) == 1
    assert "aws ec2 describe-instances" in code[code.index(needle) - 40:code.index(needle)]
    assert 'send-command --instance-ids "$iid"' in code
    mutating = ("send-command", "terminate-instances", "stop-instances", "reboot-instances", "start-session", "create-tags")
    for line in _code(ORCHESTRATOR).replace("\\\n", " ").splitlines():
        if "prod_instance" in line:
            assert not any(verb in line for verb in mutating), line


def test_no_drill_script_can_print_a_secret():
    for path in [*SHELL_SCRIPTS, CHECKS]:
        code = _code(path)
        assert not re.search(r"\bset\s+-\w*x", code) and "xtrace" not in code and "bash -x" not in code, path.name
    onbox = _joined(ONBOX)
    assert onbox.count("--with-decryption") == 1
    assert re.search(r"--with-decryption[^;|)]*>\.env", onbox), "the production env must go to a file, never stdout"
    assert not re.search(r"\bcat\s+\S*\.env\b", onbox) and not re.search(r"(\bsource|\s\.)\s+\S*\.env\b", onbox)
    assert "--password-stdin" in onbox and "unset ghcr_token" in onbox
    assert "| redact" in _code(ORCHESTRATOR), "the report passes through redact() before it is saved or shown"


def test_the_expected_offline_list_is_explicit_and_names_real_checks():
    assert set(checks.EXPECTED_OFFLINE) == {"chat", "pipeline_generate", "webhooks", "backup_fresh"}
    assert all(checks.EXPECTED_OFFLINE.values()), "every exemption carries its reason"
    assert set(checks.EXPECTED_OFFLINE) <= set(_verifier_names()), "an exemption for a check that was renamed hides nothing"
    readme = _text(DEPLOY / "README.md")
    assert all(name in readme for name in checks.EXPECTED_OFFLINE)


def test_the_required_checks_agree_across_the_scripts_and_the_checks_module():
    in_orchestrator = re.search(r'^REQUIRED_CHECKS="(.*)"$', _text(ORCHESTRATOR), re.M).group(1).split()
    assert in_orchestrator == REQUIRED
    emitted = {name for name, _, _ in checks.judge_data({"databases": {}}, ())[0] + checks.judge_live("")[0]}
    assert emitted <= set(REQUIRED), "restore-drill-checks.py reports a check the verdict does not require"


def test_the_drill_counts_the_directories_backup_sh_archives():
    backup = _text(DEPLOY / "backup.sh")
    archived = tuple(re.search(r'^FILE_DIRS="([^"]*)"', backup, re.M).group(1).split())
    assert checks.FILE_DIRS == archived


def test_the_payload_embedded_in_the_ssm_command_stays_small():
    packed = sum(len(base64.b64encode(gzip.compress(path.read_bytes()))) for path in (ONBOX, CHECKS))
    # A Windows command line holds 32 KB in total, and this travels as one argument to aws.exe.
    assert packed < 16_000, f"{packed} bytes of base64 payload"


def test_the_readme_documents_the_drill():
    readme = _text(DEPLOY / "README.md")
    section = readme[readme.index("### Restore drill"):]
    for needle in ("restore-drill.sh", "--prod-instance", "internal: true", "cents", "restore-drill-<stamp>.log"):
        assert needle in section, needle
    assert (DEPLOY / "restore-drill.sh").exists()


@pytest.mark.parametrize("path", [*SHELL_SCRIPTS, CHECKS], ids=lambda p: p.name)
def test_drill_files_are_lf_only(path):
    assert b"\r" not in path.read_bytes()


def _verifier_names() -> list[str]:
    return re.findall(r'^\s+\("([\w:]+)",\s*check_\w+\),?\s*$', _text(VERIFIER), re.M)


# ── bash: sourcing the scripts and driving their functions ──────────────────


def _working_bash():
    """A bash that can run these scripts, or None. On Windows `bash` can be the WSL launcher, which cannot read
    C:/ paths, so only a Windows-native bash (Git Bash, Cygwin) is accepted there."""
    seen = set()
    for exe in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if not exe or exe in seen or not Path(exe).exists():
            continue
        seen.add(exe)
        try:
            probe = subprocess.run([exe, "-c", 'echo "ok:$OSTYPE"'], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        kind = probe.stdout.strip()
        if probe.returncode == 0 and kind.startswith("ok:") and (os.name != "nt" or kind[3:] in ("msys", "cygwin")):
            return exe
    return None


BASH = _working_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no usable bash on this machine")


def _posix(path) -> str:
    return Path(path).as_posix()


def _env(bin_dir=None, **extra) -> dict:
    """Nothing AWS-shaped leaks in; with a shim directory, a fail-closed `aws` is found before any real one."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "AURA_"))}
    env.update(AWS_EC2_METADATA_DISABLED="true", AWS_ACCESS_KEY_ID="invalid", AWS_SECRET_ACCESS_KEY="invalid")
    env.update(AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull)
    if bin_dir is not None:
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env.update(extra)
    return env


def _bash(snippet: str, *, env=None, cwd=None, timeout=180):
    return subprocess.run([BASH, "-c", snippet], capture_output=True, text=True, env=env or _env(), cwd=cwd, timeout=timeout)


@pytest.fixture
def aws_shim(tmp_path):
    """An `aws` that refuses to run. Every bash test that could reach `aws` has it first on PATH."""
    bin_dir = tmp_path / "shim_bin"
    bin_dir.mkdir()
    log = tmp_path / "shim_calls.log"
    shim = bin_dir / "aws"
    shim.write_text(
        '#!/bin/sh\necho "$@" >> "$AWS_SHIM_LOG"\necho "aws shim: no real AWS command may run in a test" >&2\nexit 97\n',
        encoding="utf-8", newline="\n",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return SimpleNamespace(bin=bin_dir, log=log, env={"AWS_SHIM_LOG": _posix(log)})


@needs_bash
@pytest.mark.parametrize("path", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_pass_bash_n(path):
    # Fed on stdin as bytes: no path to convert, and Windows text mode cannot turn LF into CRLF.
    result = subprocess.run([BASH, "-n"], input=path.read_bytes(), capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()


@needs_bash
def test_the_inline_policy_only_reads_the_backup_bucket_and_the_one_env_parameter():
    out = _bash(f'source "{_posix(ORCHESTRATOR)}"; REGION=us-east-1; role_policy my-bucket 123456789012 /aura/prod/env; echo; echo "$TRUST_POLICY"')
    policy_text, trust_text = out.stdout.strip().split("\n")
    statements = json.loads(policy_text)["Statement"]
    assert {(s["Action"], s["Resource"]) for s in statements} == {
        ("s3:ListBucket", "arn:aws:s3:::my-bucket"),
        ("s3:GetObject", "arn:aws:s3:::my-bucket/*"),
        ("ssm:GetParameter", "arn:aws:ssm:us-east-1:123456789012:parameter/aura/prod/env"),
    }
    assert all(s["Effect"] == "Allow" for s in statements)
    trust = json.loads(trust_text)["Statement"]
    assert trust == [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]


@needs_bash
def test_the_ssm_command_unpacks_both_files_byte_for_byte_and_passes_only_validated_values():
    out = _bash(f'source "{_posix(ORCHESTRATOR)}"; build_command_parameters us-east-1 my-bucket {STAMP} /aura/prod/env')
    assert out.returncode == 0, out.stderr
    params = json.loads(out.stdout)
    assert params["executionTimeout"] == ["3600"]
    mkdir, onbox, checks_cmd, run = params["commands"]
    assert mkdir == "mkdir -p /root/drill"
    for command, source, target in ((onbox, ONBOX, "restore-drill-onbox.sh"), (checks_cmd, CHECKS, "restore-drill-checks.py")):
        match = re.fullmatch(r"echo (\S+) \| base64 -d \| gunzip > /root/drill/(\S+)", command)
        assert match and match.group(2) == target
        assert gzip.decompress(base64.b64decode(match.group(1))) == source.read_bytes()
    assert run == (
        f"AURA_DRILL_REGION=us-east-1 AURA_DRILL_BUCKET=my-bucket AURA_DRILL_STAMP={STAMP} "
        "AURA_ENV_SSM_PARAM=/aura/prod/env bash /root/drill/restore-drill-onbox.sh"
    )


@needs_bash
def test_redact_masks_key_shaped_strings_and_nothing_else():
    secrets = [
        "gsk_" + "a" * 40, "sk-ant-" + "b" * 40, "sk-proj-" + "c" * 40, "AIza" + "d" * 35,
        "ghp_" + "e" * 36, "AKIA" + "F" * 16, "xoxb-" + "1234567890" * 2,
    ]
    # An ordinary word that ends in "sk" must survive. Written in pieces: scripts/hooks/block_secrets.py has no
    # word-boundary rule and would read the whole word as a key.
    word_ending_in_sk = "ta" + "sk-" + "management-integration-tests-for-everything"
    keep = ["a" * 40, word_ending_in_sk, "build=0123456789abcdef0123456789abcdef01234567"]
    text = "\n".join(f"line {i} {s} end" for i, s in enumerate(secrets + keep))
    out = subprocess.run(
        [BASH, "-c", f'source "{_posix(ORCHESTRATOR)}"; redact'], input=text, capture_output=True, text=True, env=_env(), timeout=60,
    )
    assert out.returncode == 0, out.stderr
    for secret in secrets:
        assert secret not in out.stdout
    assert out.stdout.count("<redacted>") == len(secrets)
    for kept in keep:
        assert kept in out.stdout


def _judge(tmp_path, report: str, ssm_status="Success"):
    report_file = tmp_path / "report.log"
    report_file.write_text(report, encoding="utf-8", newline="\n")
    return _bash(f'source "{_posix(ORCHESTRATOR)}"; judge_report "{_posix(report_file)}" {ssm_status}')


def _onbox_report(overrides=None, notes=()) -> str:
    """The closing block the REAL on-box script prints, so the judge is tested against what it will receive."""
    overrides = overrides or {}
    lines = [f'source "{_posix(ONBOX)}"']
    for name in REQUIRED:
        status, detail = overrides.get(name, ("PASS", f"{name} ok"))
        if status is not None:
            lines.append(f'record {name} {status} "{detail}" >/dev/null')
    lines += [f'note "{n}" >/dev/null' for n in notes] + ["finish"]
    result = _bash("\n".join(lines))
    assert result.returncode == 0, result.stderr
    return result.stdout


@needs_bash
def test_judge_passes_a_clean_report(tmp_path):
    result = _judge(tmp_path, _onbox_report(notes=["saved_credentials: 3 decrypted, 0 failed"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(name in result.stdout for name in REQUIRED)


@needs_bash
@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize("status", ["FAIL", "SKIP", None], ids=["failed", "skipped", "never-ran"])
def test_judge_fails_when_any_required_check_is_not_a_pass(tmp_path, name, status):
    result = _judge(tmp_path, _onbox_report({name: (status, "broken")}))
    assert result.returncode == 1, result.stdout
    assert re.search(rf"(FAIL|SKIP|MISSING)\s+{name}\b", result.stdout)


@needs_bash
def test_judge_fails_without_the_closing_sentinel_or_with_a_bad_ssm_status(tmp_path):
    clean = _onbox_report()
    truncated = clean.replace("DRILL_END\n", "")
    assert _judge(tmp_path, truncated).returncode == 1
    for status in ("Failed", "TimedOut", "Cancelled", "Pending"):
        assert _judge(tmp_path, clean, status).returncode == 1, status


@needs_bash
def test_judge_never_lets_an_earlier_pass_outvote_a_failure(tmp_path):
    clean = _onbox_report()
    forged_pass = "DRILL_CHECK restore PASS forged\n" + _onbox_report({"restore": ("FAIL", "real failure")})
    assert _judge(tmp_path, forged_pass).returncode == 1
    unknown_failure = clean.replace("DRILL_VERDICT", "DRILL_CHECK surprise FAIL not in the required list\nDRILL_VERDICT")
    assert _judge(tmp_path, unknown_failure).returncode == 1


FAKE_AWS = r"""
aws() {
  printf '@@CALL@@ %s\n' "$*" >>"$AWS_CALLS"
  local prev="" arg
  for arg in "$@"; do
    case $prev in
      --user-data) printf '%s' "$arg" >"$STATE/userdata.txt" ;;
      --parameters) printf '%s' "$arg" >"$STATE/params.json" ;;
    esac
    prev=$arg
  done
  if [ -n "${FAIL_ON:-}" ]; then
    case "$*" in *"$FAIL_ON"*) echo "An error occurred (AccessDenied) when calling the operation" >&2; return 255 ;; esac
  fi
  if [ -n "${IAM_ABSENT:-}" ]; then
    case "$*" in
      "iam remove-"*|"iam delete-"*|"iam detach-"*) echo "An error occurred (NoSuchEntity) when calling the operation" >&2; return 254 ;;
    esac
  fi
  if [ -n "${SIGNAL_AT:-}" ]; then
    case "$*" in *"$SIGNAL_AT"*) kill -"${SIGNAL_WITH:-INT}" $$ ;; esac
  fi
  case "$*" in
    "sts get-caller-identity "*) echo 123456789012 ;;
    "ec2 describe-instances --instance-ids "*) printf 'subnet-0abc1234\tvpc-0abc1234\n' ;;
    "ec2 describe-instances --filters "*) if [ -e "$STATE/instance" ]; then echo i-0fedcba987654321; fi ;;
    "s3 ls s3://my-bucket/") printf '                           PRE 20261125T031500Z/\n                           PRE 20261126T031500Z/\n' ;;
    "s3 ls s3://my-bucket/"*) echo "2026-11-26 03:15:00        123 files.tar.gz" ;;
    "ec2 create-security-group "*) : >"$STATE/sg"; echo sg-0bbb2222 ;;
    "ec2 run-instances "*) : >"$STATE/instance"; echo i-0fedcba987654321 ;;
    "ec2 wait instance-terminated "*) rm -f "$STATE/instance" ;;
    "ssm describe-instance-information "*) echo Online ;;
    "ssm send-command "*) echo 11111111-2222-3333-4444-555555555555 ;;
    "ssm get-command-invocation "*"--query Status --output text") echo "${SSM_STATUS:-Success}" ;;
    "ssm get-command-invocation "*"--query StandardOutputContent --output text") cat "$REPORT_SRC" ;;
    "ec2 describe-security-groups "*) if [ -e "$STATE/sg" ]; then echo sg-0bbb2222; else echo None; fi ;;
    "ec2 delete-security-group "*)
      n=$(cat "$STATE/sgbusy" 2>/dev/null || echo 0)
      if [ "$n" -lt "${SG_BUSY:-0}" ]; then
        echo $((n + 1)) >"$STATE/sgbusy"
        echo "An error occurred (DependencyViolation) when calling the DeleteSecurityGroup operation" >&2
        return 255
      fi
      rm -f "$STATE/sg" ;;
  esac
  return 0
}
"""


def _run_with_fake_aws(tmp_path, aws_shim, body: str, *, report="", markers=(), **knobs):
    """Runs `body` (bash) with the orchestrator sourced and `aws` replaced by FAKE_AWS. Returns what happened.

    `markers` are the resources the fake account already holds ("instance", "sg"); every run starts from a
    clean state directory and call log."""
    state, work = tmp_path / "state", tmp_path / "work"
    shutil.rmtree(state, ignore_errors=True)
    state.mkdir()
    work.mkdir(exist_ok=True)
    for marker in markers:
        (state / marker).write_text("")
    report_src = tmp_path / "report_src.txt"
    report_src.write_text(report, encoding="utf-8", newline="\n")
    calls_log = tmp_path / "aws_calls.log"
    calls_log.unlink(missing_ok=True)
    env = _env(aws_shim.bin, STATE=_posix(state), AWS_CALLS=_posix(calls_log), REPORT_SRC=_posix(report_src), **aws_shim.env, **knobs)
    snippet = f'source "{_posix(ORCHESTRATOR)}"\nsleep() {{ :; }}\n{FAKE_AWS}\ncd "{_posix(work)}"\n{body}\n'
    result = _bash(snippet, env=env)
    raw = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
    calls = [call.rstrip("\n") for call in raw.split("@@CALL@@ ")[1:]]
    assert not aws_shim.log.exists(), "a real aws call escaped the fake: " + aws_shim.log.read_text()
    return SimpleNamespace(
        rc=result.returncode, out=result.stdout, err=result.stderr, calls=calls, work=work, state=state,
        ops=[" ".join(call.split()[:2]) for call in calls],
    )


TEARDOWN = 'NAME=aura-restore-drill-test VPC_ID=vpc-0abc1234 REGION=us-east-1 REPORT=""\nteardown\necho "LEAKED=[$LEAKED]"'


@needs_bash
def test_teardown_terminates_waits_then_deletes_profile_role_and_security_group(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, TEARDOWN, markers=("instance", "sg"))
    assert run.rc == 0 and "LEAKED=[]" in run.out, run.out + run.err
    waits = [i for i, call in enumerate(run.calls) if call.startswith("ec2 wait instance-terminated")]
    order = [
        run.ops.index("ec2 describe-instances"), run.ops.index("ec2 terminate-instances"), waits[0],
        run.ops.index("iam remove-role-from-instance-profile"), run.ops.index("iam delete-instance-profile"),
        run.ops.index("iam delete-role-policy"), run.ops.index("iam detach-role-policy"), run.ops.index("iam delete-role"),
        run.ops.index("ec2 delete-security-group"),
    ]
    assert order == sorted(order), run.ops
    assert f"--instance-ids {DRILL_INSTANCE}" in run.calls[order[1]]


@needs_bash
def test_teardown_treats_never_created_resources_as_already_gone(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, TEARDOWN, IAM_ABSENT="1")
    assert run.rc == 0 and "LEAKED=[]" in run.out, run.out + run.err
    assert "ec2 terminate-instances" not in run.ops and "ec2 delete-security-group" not in run.ops
    assert run.out.count("already gone") == 5


@needs_bash
def test_teardown_reports_what_it_could_not_remove(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, TEARDOWN, markers=("sg",), FAIL_ON="iam delete-role --role-name")
    assert "LEAKED=[ role aura-restore-drill-test;]" in run.out, run.out + run.err
    assert "ec2 delete-security-group" in run.ops, "one failed step must not stop the rest of the teardown"


@needs_bash
def test_teardown_retries_a_security_group_whose_network_interface_lingers(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, TEARDOWN, markers=("sg",), SG_BUSY="3")
    assert run.rc == 0 and "LEAKED=[]" in run.out, run.out + run.err
    assert run.ops.count("ec2 delete-security-group") == 4
    stuck = _run_with_fake_aws(tmp_path, aws_shim, TEARDOWN, markers=("sg",), SG_BUSY="999")
    assert "security group sg-0bbb2222" in stuck.out.split("LEAKED=")[1], stuck.out
    assert stuck.ops.count("ec2 delete-security-group") == 24


@needs_bash
@pytest.mark.parametrize(
    ("result", "leaked", "expected"),
    [("pass", "", 0), ("fail", "", 1), ("pass", " role x;", 3), ("fail", " role x;", 3)],
)
def test_exit_status_contract(tmp_path, aws_shim, result, leaked, expected):
    setup = f'NAME=n VPC_ID=vpc-0abc1234 REGION=us-east-1 REPORT=r.log RESULT={result} LEAKED="{leaked}"'
    run = _run_with_fake_aws(tmp_path, aws_shim, f"{setup}\ncleanup")
    assert run.rc == expected, run.out + run.err


@needs_bash
@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "--prod-instance is required"),
        (["--prod-instance"], "needs a value"),
        (["--prod-instance", "prod-box"], "--prod-instance is required"),
        (["--prod-instance", PROD_INSTANCE, "--region", "us_east_1"], "--region"),
        (["--prod-instance", PROD_INSTANCE, "--bucket", "Not_A_Bucket"], "--bucket"),
        (["--prod-instance", PROD_INSTANCE, "--bucket", "ok-bucket;touch pwned"], "--bucket"),
        (["--prod-instance", PROD_INSTANCE, "--stamp", "yesterday"], "--stamp"),
        (["--prod-instance", PROD_INSTANCE, "--stamp", "20261126T031500Z$(touch pwned)"], "--stamp"),
        (["--prod-instance", PROD_INSTANCE, "--frobnicate"], "unknown argument"),
    ],
)
def test_bad_arguments_exit_2_before_any_aws_call(tmp_path, aws_shim, args, message):
    result = subprocess.run(
        [BASH, _posix(ORCHESTRATOR), *args], capture_output=True, text=True, cwd=tmp_path, timeout=60, env=_env(aws_shim.bin, **aws_shim.env),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert message in result.stderr
    assert not aws_shim.log.exists(), "validation must finish before the first aws call"
    assert not (tmp_path / "pwned").exists()


@needs_bash
def test_a_bad_environment_value_is_rejected_too(tmp_path, aws_shim):
    env = _env(aws_shim.bin, AURA_ENV_SSM_PARAM="/aura/prod/env;touch pwned", **aws_shim.env)
    result = subprocess.run(
        [BASH, _posix(ORCHESTRATOR), "--prod-instance", PROD_INSTANCE], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60,
    )
    assert result.returncode == 2 and "AURA_ENV_SSM_PARAM" in result.stderr and not aws_shim.log.exists()
    assert not (tmp_path / "pwned").exists()


@needs_bash
def test_help_prints_usage_and_exits_0_without_touching_aws(tmp_path, aws_shim):
    result = subprocess.run([BASH, _posix(ORCHESTRATOR), "--help"], capture_output=True, text=True, env=_env(aws_shim.bin, **aws_shim.env), timeout=60)
    assert result.returncode == 0 and "--prod-instance" in result.stdout and not aws_shim.log.exists()


# ── bash: the on-box verdict block ──────────────────────────────────────────


@needs_bash
def test_onbox_reports_every_required_check_as_failed_when_nothing_ran():
    out = _bash(f'source "{_posix(ONBOX)}"\nCURRENT_STEP="the restore"\nconclude')
    assert out.returncode == 1
    for name in REQUIRED:
        assert f"DRILL_CHECK {name} SKIP not reached (stopped during: the restore)" in out.stdout
    assert out.stdout.rstrip().endswith("DRILL_VERDICT FAIL\nDRILL_END")


@needs_bash
def test_onbox_passes_only_when_every_required_check_passed():
    report = _onbox_report(notes=["live check chat failed offline, as expected: it calls an LLM provider"])
    assert "DRILL_VERDICT PASS" in report and report.rstrip().endswith("DRILL_END")
    assert "DRILL_NOTE live check chat failed offline" in report
    assert all(f"DRILL_CHECK {name} PASS" in report for name in REQUIRED)
    assert "DRILL_VERDICT FAIL" in _onbox_report({"ledger_chains": ("FAIL", "1 of 8 chains failed")})
    skipped = _onbox_report({"live_checks": ("SKIP", "not run: the app never became healthy")})
    assert "DRILL_VERDICT FAIL" in skipped, "a check that was skipped proved nothing"


@needs_bash
def test_onbox_gates_on_a_check_it_did_not_expect_and_prints_the_verdict_once(tmp_path):
    lines = tmp_path / "from_checks.txt"
    lines.write_text("DRILL_CHECK db_integrity PASS 5 databases ok\nDRILL_CHECK surprise FAIL a new gating check\nDRILL_NOTE files: keys=3\n", encoding="utf-8")
    out = _bash(f'source "{_posix(ONBOX)}"\nabsorb "{_posix(lines)}" >/dev/null\nfinish\nfinish')
    assert "DRILL_CHECK surprise FAIL a new gating check" in out.stdout
    assert "DRILL_CHECK db_integrity PASS 5 databases ok" in out.stdout and "DRILL_NOTE files: keys=3" in out.stdout
    assert out.stdout.count("DRILL_VERDICT") == 1 and "DRILL_VERDICT FAIL" in out.stdout


@needs_bash
def test_onbox_still_prints_a_verdict_when_it_dies_early():
    out = _bash(f'source "{_posix(ONBOX)}"\ntrap on_exit EXIT\nCURRENT_STEP="start the restored app"\nexit 3')
    assert out.returncode == 1
    assert "drill script stopped unexpectedly during: start the restored app" in out.stdout
    assert "DRILL_VERDICT FAIL" in out.stdout and "DRILL_END" in out.stdout


@needs_bash
@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "AURA_DRILL_REGION and AURA_DRILL_BUCKET must be set"),
        ({"AURA_DRILL_REGION": "us-east-1", "AURA_DRILL_BUCKET": "my-bucket", "AURA_DRILL_STAMP": "../../etc"}, "AURA_DRILL_STAMP must be a backup timestamp"),
        ({"AURA_DRILL_REGION": "us-east-1", "AURA_DRILL_BUCKET": "my-bucket"}, "AURA_DRILL_STAMP must be a backup timestamp"),
    ],
)
def test_onbox_refuses_bad_parameters_before_touching_anything(env, message):
    # Runs the real script. It must stop at parameter validation, long before it would clone or restore anything.
    result = subprocess.run([BASH, _posix(ONBOX)], capture_output=True, text=True, env=_env(**env), timeout=60)
    assert result.returncode == 1
    assert f"ABORT: {message}" in result.stdout
    assert "DRILL_VERDICT FAIL" in result.stdout and result.stdout.rstrip().endswith("DRILL_END")


# ── bash: one whole simulated drill, against the fake aws ───────────────────

DRILL = f"main --prod-instance {PROD_INSTANCE} --bucket my-bucket"


def _all_calls_text(run) -> str:
    return "\n".join(run.calls)


@needs_bash
def test_a_passing_drill_creates_restores_and_removes_everything(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report())
    assert run.rc == 0, run.out + run.err
    assert f"restore-drill: PASSED. Report: restore-drill-{STAMP}.log" in run.out  # the newest stamp, found by listing the bucket
    report = (run.work / f"restore-drill-{STAMP}.log").read_text(encoding="utf-8")
    assert "DRILL_END" in report and report.rstrip().endswith("# teardown complete: instance terminated; profile, role and security group deleted")

    ops = run.ops
    created = ["iam create-role", "iam attach-role-policy", "iam put-role-policy", "iam create-instance-profile",
               "iam add-role-to-instance-profile", "ec2 create-security-group", "ec2 run-instances", "ssm send-command"]
    assert [ops.index(op) for op in created] == sorted(ops.index(op) for op in created)
    removed = ["ec2 terminate-instances", "ec2 wait", "iam remove-role-from-instance-profile", "iam delete-instance-profile",
               "iam delete-role-policy", "iam detach-role-policy", "iam delete-role", "ec2 delete-security-group"]
    positions = [len(ops) - 1 - ops[::-1].index(op) for op in removed]
    assert positions == sorted(positions) and positions[0] > ops.index("ssm send-command")
    assert "ec2 wait instance-terminated" in _all_calls_text(run)


@needs_bash
def test_the_drill_instance_is_the_only_target_and_production_is_only_described(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL + f" --stamp {STAMP}", report=_onbox_report())
    assert run.rc == 0, run.out + run.err
    mentioning_prod = [call for call in run.calls if PROD_INSTANCE in call]
    assert len(mentioning_prod) == 1 and mentioning_prod[0].startswith("ec2 describe-instances --instance-ids")
    (send,) = [call for call in run.calls if call.startswith("ssm send-command")]
    assert f"--instance-ids {DRILL_INSTANCE}" in send and PROD_INSTANCE not in send
    text = _all_calls_text(run)
    assert "authorize-security-group-ingress" not in text and "--key-name" not in text


@needs_bash
def test_the_launch_carries_the_dead_man_switch_and_the_lockdown_flags(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report())
    (launch,) = [call for call in run.calls if call.startswith("ec2 run-instances")]
    for flag in ("--instance-type t3.small", "--instance-initiated-shutdown-behavior terminate", "HttpTokens=required",
                 "Encrypted=true", "DeleteOnTermination=true", "Value=temporary-restore-drill", "--associate-public-ip-address"):
        assert flag in launch, flag
    sent = (run.state / "userdata.txt").read_text(encoding="utf-8")
    assert sent == _text(USERDATA).rstrip("\n"), "the instance must boot the committed user-data, unchanged"
    assert "shutdown -h +150" in sent
    params = json.loads((run.state / "params.json").read_text(encoding="utf-8"))
    assert f"AURA_DRILL_STAMP={STAMP}" in params["commands"][-1]


@needs_bash
def test_a_failing_check_fails_the_drill_but_still_tears_everything_down(tmp_path, aws_shim):
    report = _onbox_report({"db_integrity": ("FAIL", "integrity_check failed: gateway.db")})
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=report)
    assert run.rc == 1, run.out + run.err
    assert "FAILED" in run.err and "db_integrity" in run.out
    for op in ("ec2 terminate-instances", "iam delete-role", "ec2 delete-security-group"):
        assert op in run.ops
    assert (run.work / f"restore-drill-{STAMP}.log").exists()


@needs_bash
@pytest.mark.parametrize("case", ["ssm-failed", "ssm-timed-out-with-no-output", "report-truncated"])
def test_a_dead_or_cut_off_drill_cannot_pass(tmp_path, aws_shim, case):
    clean = _onbox_report()
    report, knobs = {
        "ssm-failed": (clean, {"SSM_STATUS": "Failed"}),
        "ssm-timed-out-with-no-output": ("", {"SSM_STATUS": "TimedOut"}),
        "report-truncated": (clean.replace("DRILL_END\n", ""), {}),
    }[case]
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=report, **knobs)
    assert run.rc == 1, run.out + run.err
    assert "ec2 terminate-instances" in run.ops and "ec2 delete-security-group" in run.ops


@needs_bash
def test_a_failed_launch_still_removes_the_role_profile_and_security_group(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report(), FAIL_ON="ec2 run-instances")
    assert run.rc == 1 and "could not launch the drill instance" in run.err, run.out + run.err
    assert "ec2 terminate-instances" not in run.ops, "there was no instance to terminate"
    for op in ("iam delete-instance-profile", "iam delete-role", "ec2 delete-security-group"):
        assert op in run.ops, op
    assert not list(run.work.glob("restore-drill-*.log"))


@needs_bash
def test_a_failure_halfway_through_creating_the_role_still_cleans_up(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report(), FAIL_ON="iam put-role-policy")
    assert run.rc == 1 and "could not attach the access policy" in run.err
    assert "iam delete-role" in run.ops and "ec2 run-instances" not in run.ops


@needs_bash
def test_a_failure_before_anything_exists_creates_and_deletes_nothing(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report(), FAIL_ON="sts get-caller-identity")
    assert run.rc == 1 and "no usable AWS session" in run.err
    assert not [op for op in run.ops if op.startswith("iam")] and "ec2 run-instances" not in run.ops


@needs_bash
def test_a_teardown_that_cannot_finish_exits_3_and_names_the_leak(tmp_path, aws_shim):
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report(), FAIL_ON="iam delete-role --role-name")
    assert run.rc == 3, run.out + run.err
    assert "TEARDOWN INCOMPLETE" in run.err and "role aura-restore-drill-" in run.err
    assert "# teardown INCOMPLETE:" in (run.work / f"restore-drill-{STAMP}.log").read_text(encoding="utf-8")


@needs_bash
@pytest.mark.parametrize("sig", ["TERM", "INT"])
def test_an_interrupt_in_the_middle_of_the_drill_still_tears_everything_down(tmp_path, aws_shim, sig):
    if sig == "INT" and signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT was ignored when this run started, and bash cannot trap a signal that was ignored on entry")
    run = _run_with_fake_aws(
        tmp_path, aws_shim, DRILL, report=_onbox_report(), SIGNAL_AT="ssm get-command-invocation", SIGNAL_WITH=sig,
    )
    assert run.rc == 1, run.out + run.err  # an interrupted drill proved nothing, so it is not a pass
    for op in ("ec2 terminate-instances", "iam delete-instance-profile", "iam delete-role", "ec2 delete-security-group"):
        assert op in run.ops, (op, run.ops)


@needs_bash
def test_an_existing_report_is_never_overwritten(tmp_path, aws_shim):
    (tmp_path / "work").mkdir()
    old = tmp_path / "work" / f"restore-drill-{STAMP}.log"
    old.write_text("evidence from an earlier run\n", encoding="utf-8")
    run = _run_with_fake_aws(tmp_path, aws_shim, DRILL, report=_onbox_report())
    assert run.rc == 0 and old.read_text(encoding="utf-8") == "evidence from an earlier run\n"
    assert len(list(run.work.glob("restore-drill-*.log"))) == 2


# ── restore-drill-checks.py ─────────────────────────────────────────────────


def _make_db(path: Path, rows=3, table="items"):
    conn = sqlite3.connect(path)
    conn.execute(f"create table {table} (id integer primary key, name text)")
    conn.execute(f"create index {table}_name on {table}(name)")
    conn.executemany(f"insert into {table} (name) values (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def test_databases_are_checked_read_only_and_counted(tmp_path):
    _make_db(tmp_path / "gateway.db", rows=3)
    _make_db(tmp_path / "ledger.db", rows=5, table="entries")
    (tmp_path / "ignored.duckdb").write_bytes(b"not sqlite and not matched")
    found = checks.check_databases(str(tmp_path))
    assert found == {
        "gateway.db": {"integrity": "ok", "tables": 1, "rows": 3},
        "ledger.db": {"integrity": "ok", "tables": 1, "rows": 5},
    }


def test_a_corrupt_database_is_reported_not_raised(tmp_path):
    _make_db(tmp_path / "metadata.db", rows=2000)
    data = bytearray((tmp_path / "metadata.db").read_bytes())
    data[4096:8192] = b"\xff" * 4096  # destroy pages after the header
    (tmp_path / "metadata.db").write_bytes(bytes(data))
    (tmp_path / "gateway.db").write_bytes(b"this is not a database" * 50)
    found = checks.check_databases(str(tmp_path))
    assert found["metadata.db"]["integrity"] != "ok"
    assert found["gateway.db"]["integrity"].startswith("error: ")


def test_counting_files_covers_the_archived_directories(tmp_path):
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "signing_ed25519.pem").write_text("x")
    (tmp_path / "uploads" / "ws").mkdir(parents=True)
    (tmp_path / "uploads" / "ws" / "a.csv").write_text("x")
    (tmp_path / "uploads" / "ws" / "b.csv").write_text("x")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "ignored").write_text("x")
    assert checks.count_files(str(tmp_path)) == {"keys": 1, "uploads": 2}


def _data_report(**over):
    report = {
        "databases": {name: {"integrity": "ok", "tables": 1, "rows": 1} for name in ("gateway.db", "ledger.db")},
        "audit_ledger": {"tenants": 8, "records": 120, "chains_ok": 8, "failures": 0},
        "saved_credentials": {"decrypted": 3, "failed": 0},
        "signing_key": "loads and signs",
        "files": {"keys": 1, "uploads": 4},
    }
    report.update(over)
    return report


def _verdicts(report, expected=()):
    found, notes = checks.judge_data(report, expected)
    return {name: (status, detail) for name, status, detail in found}, notes


def test_a_sound_restore_passes_both_gating_checks():
    verdict, notes = _verdicts(_data_report(), expected=["gateway.db", "ledger.db"])
    assert verdict["db_integrity"][0] == "PASS" and "2 databases ok" in verdict["db_integrity"][1]
    assert verdict["ledger_chains"] == ("PASS", "8 chains, 120 records, 0 failures")
    assert "saved_credentials: 3 decrypted, 0 failed" in notes and "signing_key: loads and signs" in notes


@pytest.mark.parametrize(
    ("report", "expected_dbs", "needle"),
    [
        (_data_report(databases={"gateway.db": {"integrity": "ok"}, "ledger.db": {"integrity": "*** in database main ***"}}), (), "ledger.db"),
        (_data_report(databases={"gateway.db": {"integrity": "error: DatabaseError"}}), (), "gateway.db"),
        (_data_report(databases={}), (), "no SQLite databases"),
        (_data_report(), ("gateway.db", "ledger.db", "uasr_state.db"), "in the backup but not restored: uasr_state.db"),
    ],
    ids=["failed-integrity", "unreadable", "restore-produced-nothing", "database-missing-after-restore"],
)
def test_any_database_problem_fails_the_integrity_check(report, expected_dbs, needle):
    verdict, _ = _verdicts(report, expected_dbs)
    assert verdict["db_integrity"][0] == "FAIL" and needle in verdict["db_integrity"][1]


@pytest.mark.parametrize(
    "ledger",
    [
        {"tenants": 8, "records": 120, "chains_ok": 7, "failures": 2},
        {"tenants": 8, "records": 120, "chains_ok": 8, "failures": 1},
        {"tenants": 8, "records": 120, "chains_ok": 6, "failures": 0},
    ],
)
def test_any_broken_ledger_chain_fails_the_drill(ledger):
    verdict, _ = _verdicts(_data_report(audit_ledger=ledger))
    assert verdict["ledger_chains"][0] == "FAIL"


def test_an_unverifiable_ledger_fails_rather_than_passing_silently():
    report = _data_report()
    del report["audit_ledger"]
    report["audit_ledger_error"] = "OperationalError: no such table: audit_ledger"
    verdict, _ = _verdicts(report)
    assert verdict["ledger_chains"][0] == "FAIL" and "no such table" in verdict["ledger_chains"][1]


def test_an_empty_ledger_passes_but_says_nothing_was_verified():
    verdict, notes = _verdicts(_data_report(audit_ledger={"tenants": 0, "records": 0, "chains_ok": 0, "failures": 0}))
    assert verdict["ledger_chains"][0] == "PASS"
    assert any("nothing was verified" in note for note in notes)


def test_credential_and_signing_key_problems_are_reported_loudly_but_do_not_gate():
    report = _data_report(saved_credentials={"decrypted": 1, "failed": 2}, signing_key="error: ValueError")
    verdict, notes = _verdicts(report)
    assert verdict["db_integrity"][0] == "PASS" and verdict["ledger_chains"][0] == "PASS"
    assert "WARN saved_credentials: 2 of 3 did not decrypt with the restored environment's key" in notes
    assert "WARN signing_key: error: ValueError" in notes
    report = _data_report(saved_credentials_error="CredentialEncryptionError: key unset")
    del report["saved_credentials"]
    assert any("not checked (CredentialEncryptionError" in note for note in _verdicts(report)[1])


def _verifier_output(failed=(), skipped=(), names=None):
    names = names or _verifier_names()
    lines = ["Verifying http://localhost:8000 ...", ""]
    for name in names:
        status = "FAIL" if name in failed else "SKIP" if name in skipped else "PASS"
        suffix = " -- ConnectError: no route to host" if status == "FAIL" else ""
        lines.append(f"  [{status}] {name} (12ms){suffix}")
    summary = f"{len(names) - len(failed) - len(skipped)} passed, {len(failed)} failed, {len(skipped)} skipped (of {len(names)})"
    return "\n".join(lines + ["", summary, "", "Report written to /tmp/drill_report.md", ""])


def test_the_four_expected_offline_failures_still_pass_the_drill():
    expected = list(checks.EXPECTED_OFFLINE)
    (check,), notes = checks.judge_live(_verifier_output(failed=expected))
    name, status, detail = check
    total = len(_verifier_names())
    assert (name, status) == ("live_checks", "PASS")
    assert detail.startswith(f"{total - 4}/{total} pass; expected offline: ")
    assert all(n in detail for n in expected)
    assert len(notes) == 4 and all("failed offline, as expected" in note for note in notes)


def test_a_drill_with_nothing_failing_passes_and_reports_the_count():
    (check,), notes = checks.judge_live(_verifier_output())
    total = len(_verifier_names())
    assert check == ("live_checks", "PASS", f"{total}/{total} pass") and notes == []


@pytest.mark.parametrize("broken", ["login", "health", "file_upload_profile", "ledger_proof", "fix:bug196_dashboard_fs_locked"])
def test_any_other_live_failure_fails_the_drill_even_next_to_the_expected_ones(broken):
    (check,), _ = checks.judge_live(_verifier_output(failed=[*checks.EXPECTED_OFFLINE, broken]))
    assert check[1] == "FAIL" and f"unexpected failures: {broken}" in check[2]


def test_skipped_checks_are_listed_but_a_skip_alone_is_not_a_failure():
    (check,), _ = checks.judge_live(_verifier_output(skipped=["fix:bug174_170_connection_settings"]))
    assert check[1] == "PASS" and "skipped: fix:bug174_170_connection_settings" in check[2]


@pytest.mark.parametrize(
    "text",
    ["", "Traceback (most recent call last):\nModuleNotFoundError: No module named 'httpx'\n"],
    ids=["no-output", "verifier-crashed"],
)
def test_a_verifier_that_produced_nothing_fails_the_drill(text):
    (check,), _ = checks.judge_live(text)
    assert check == ("live_checks", "FAIL", "the live verifier did not run to completion")


def test_a_verifier_cut_off_part_way_fails_the_drill():
    full = _verifier_output(failed=list(checks.EXPECTED_OFFLINE))
    no_summary = "\n".join(line for line in full.splitlines() if "passed," not in line)
    assert checks.judge_live(no_summary)[0][0][1] == "FAIL"
    partial = "\n".join(full.splitlines()[:6] + full.splitlines()[-4:])
    assert checks.judge_live(partial)[0][0][1] == "FAIL"


def test_emit_prints_one_machine_readable_line_per_check_and_sets_the_exit_status(capsys):
    assert checks.emit([("a", "PASS", "fine\nwith a newline"), ("b", "PASS", "ok")], ["a note\nacross lines"]) == 0
    assert capsys.readouterr().out.splitlines() == ["DRILL_CHECK a PASS fine with a newline", "DRILL_CHECK b PASS ok", "DRILL_NOTE a note across lines"]
    assert checks.emit([("a", "PASS", "x"), ("b", "FAIL", "y")], []) == 1
    assert checks.emit([("a", "SKIP", "x")], []) == 1


def test_error_messages_are_masked_before_they_reach_the_report():
    secret = "Zm9vYmFy" * 6
    message = checks._safe(ValueError(f"cannot use key {secret} for tenant ok"))
    assert secret not in message and message == "ValueError: cannot use key <redacted> for tenant ok"
    assert len(checks._safe(RuntimeError("word " * 200))) == len("RuntimeError: ") + 160


def test_main_data_prints_the_report_then_the_verdict_and_fails_on_a_bad_chain(monkeypatch, capsys):
    monkeypatch.setattr(sys, "path", list(sys.path))  # main() puts the working directory first; undone afterwards
    monkeypatch.setattr(checks, "collect", lambda: _data_report(audit_ledger={"tenants": 2, "records": 4, "chains_ok": 1, "failures": 1}))
    monkeypatch.setenv("DRILL_EXPECT_DBS", "gateway.db ledger.db")
    assert checks.main(["checks", "data"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out[: out.index("DRILL_CHECK")])["signing_key"] == "loads and signs"
    assert "DRILL_CHECK db_integrity PASS" in out and "DRILL_CHECK ledger_chains FAIL 1 of 2 chains failed" in out


def test_main_live_reads_the_verifier_output_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(_verifier_output(failed=list(checks.EXPECTED_OFFLINE))))
    assert checks.main(["checks", "live"]) == 0
    assert "DRILL_CHECK live_checks PASS" in capsys.readouterr().out


def test_main_rejects_an_unknown_mode(capsys):
    assert checks.main(["checks", "frobnicate"]) == 2
    assert "data" in capsys.readouterr().err


@pytest.fixture
def http_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.server.requests.append((self.command, self.path, self.rfile.read(length) if length else b""))
            self.send_response(self.server.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.server.body)))
            self.end_headers()
            self.wfile.write(self.server.body)

        do_GET = do_POST = _reply

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.code, server.body, server.requests = 200, b"{}", []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_health_probe_reports_a_healthy_gateway(http_server):
    server, base = http_server
    server.body = b'{"status": "healthy", "build": "abc123", "ops": {"backup": "missing"}, "checks": {"db": "ok", "data_dir": "ok"}}'
    code, status, summary = checks.probe_health(base + "/health")
    assert (code, status) == (200, "healthy")
    assert summary == '200 healthy build=abc123 ops={"backup": "missing"} checks={"db": "ok", "data_dir": "ok"}'


def test_health_probe_reads_the_body_of_a_503_so_the_failing_probe_is_named(http_server):
    server, base = http_server
    server.code = 503
    server.body = b'{"status": "degraded", "build": "abc123", "checks": {"db": "failing", "data_dir": "ok"}}'
    code, status, summary = checks.probe_health(base + "/health")
    assert (code, status) == (503, "degraded") and '"db": "failing"' in summary


def test_health_probe_survives_a_body_that_is_not_json(http_server):
    server, base = http_server
    server.body = b"<html>bad gateway</html>"
    assert checks.probe_health(base + "/health")[:2] == (200, None)


def test_health_probe_raises_when_nothing_is_listening(http_server):
    server, base = http_server
    server.shutdown()
    server.server_close()
    with pytest.raises(OSError):
        checks.probe_health(base + "/health")


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [(200, "healthy", 0), (200, "degraded", 1), (503, "degraded", 1), (503, "healthy", 1)],
)
def test_main_health_exits_0_only_for_a_200_healthy(monkeypatch, capsys, code, status, expected):
    monkeypatch.setattr(checks, "probe_health", lambda: (code, status, f"{code} {status}"))
    assert checks.main(["checks", "health"]) == expected
    assert capsys.readouterr().out.strip() == f"{code} {status}"


def test_main_health_exits_1_quietly_while_the_app_is_still_starting(monkeypatch, capsys):
    def refuse():
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(checks, "probe_health", refuse)
    assert checks.main(["checks", "health"]) == 1
    assert capsys.readouterr().out == ""


def test_isolation_probe_passes_when_both_probes_have_no_route(monkeypatch, capsys):
    def no_route(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(checks.urllib.request, "urlopen", no_route)
    monkeypatch.setattr(checks.socket, "create_connection", no_route)
    assert checks.probe_isolation() == 0
    out = capsys.readouterr().out
    assert out.count("no route out (as intended)") == 2 and "REACHABLE" not in out


@pytest.mark.parametrize("reachable", ["urlopen", "create_connection"])
def test_isolation_probe_fails_if_either_probe_gets_out(monkeypatch, capsys, reachable):
    def no_route(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(checks.urllib.request, "urlopen", no_route)
    monkeypatch.setattr(checks.socket, "create_connection", no_route)
    monkeypatch.setattr(checks.urllib.request if reachable == "urlopen" else checks.socket, reachable, lambda *a, **k: object())
    assert checks.probe_isolation() == 3
    assert "REACHABLE -- isolation FAILED" in capsys.readouterr().out


def test_registering_the_throwaway_account_keeps_its_credentials_private(http_server, tmp_path):
    server, base = http_server
    server.code, server.body = 201, b'{"status": "ok"}'
    creds = tmp_path / "drill_creds"
    checks.register_account(base, str(creds))
    email, password = creds.read_text(encoding="utf-8").split("\n")[:2]
    ((method, path, body),) = server.requests
    assert (method, path) == ("POST", "/api/v1/auth/register")
    assert json.loads(body) == {"email": email, "password": password, "name": "Drill"}
    assert re.fullmatch(r"verify_[0-9a-f]{8}@example\.com", email) and len(password) > 20
    if os.name != "nt":
        assert stat.S_IMODE(creds.stat().st_mode) == 0o600


def test_a_refused_registration_leaves_no_credentials_and_main_prints_no_secret(http_server, tmp_path, monkeypatch, capsys):
    server, base = http_server
    server.code, server.body = 400, b'{"detail": "password too weak"}'
    creds = tmp_path / "drill_creds"
    with pytest.raises(urllib.error.HTTPError):
        checks.register_account(base, str(creds))
    assert not creds.exists()
    register = checks.register_account
    monkeypatch.setattr(checks, "register_account", lambda: register(base, str(creds)))
    assert checks.main(["checks", "register"]) == 1
    out = capsys.readouterr().out
    assert out.strip() == "register failed: HTTPError 400"
    sent_password = json.loads(server.requests[-1][2])["password"]
    assert sent_password not in out


def test_the_signing_key_round_trip_passes_for_a_real_key_and_fails_for_a_broken_one(tmp_path):
    good = tmp_path / "signing_ed25519.pem"
    good.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    assert checks.check_signing_key(str(good)) == "loads and signs"
    bad = tmp_path / "broken.pem"
    bad.write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n")
    with pytest.raises(ValueError):
        checks.check_signing_key(str(bad))


def _gateway_connections(path: Path, tokens):
    conn = sqlite3.connect(path)
    conn.execute("create table gateway_connections (id integer primary key, password_encrypted text, config_encrypted text)")
    conn.executemany("insert into gateway_connections (password_encrypted, config_encrypted) values (?, ?)", tokens)
    conn.commit()
    conn.close()


def test_saved_credentials_are_counted_by_whether_the_restored_key_decrypts_them(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_fernet", Fernet(Fernet.generate_key()))
    good = [credentials.encrypt_secret(f"pw-{i}") for i in range(3)]
    db = tmp_path / "gateway.db"
    _gateway_connections(db, [(good[0], good[1]), (good[2], None), (None, None)])
    assert checks.check_saved_credentials(str(db)) == {"decrypted": 3, "failed": 0}
    # The same data under a different key is exactly what restoring a stale .env copy would produce.
    monkeypatch.setattr(credentials, "_fernet", Fernet(Fernet.generate_key()))
    assert checks.check_saved_credentials(str(db)) == {"decrypted": 0, "failed": 3}


@pytest_asyncio.fixture
async def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ledger_under_test.db"
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield db
    await L.close_database()


async def _append(tenant: str, count: int):
    for i in range(count):
        await L.append_audit(
            tenant_id=tenant, kind="fairness_audit_completed", subject_id=f"model-{i}", subject_type="model",
            preparer_id="ada", cert_hash=f"{i:x}" * 64, input_fingerprint="b" * 64, payload={"verdict": "ok"},
        )


async def test_the_ledger_check_verifies_every_tenant_chain_and_catches_a_tampered_record(ledger_db):
    await _append("orgA", 3)
    await _append("orgB", 2)
    assert await checks.check_ledger() == {"tenants": 2, "records": 5, "chains_ok": 2, "failures": 0}
    async with L.session_scope() as session:
        await session.execute(
            update(L.AuditLedgerRow).where(L.AuditLedgerRow.tenant_id == "orgA", L.AuditLedgerRow.seq == 1)
            .values(payload_json='{"verdict":"TAMPERED"}')
        )
    tampered = await checks.check_ledger()
    assert tampered["tenants"] == 2 and tampered["chains_ok"] == 1 and tampered["failures"] >= 2
    verdict, _ = _verdicts(_data_report(audit_ledger=tampered))
    assert verdict["ledger_chains"][0] == "FAIL"


async def test_an_empty_ledger_has_no_chains_to_check(ledger_db):
    assert await checks.check_ledger() == {"tenants": 0, "records": 0, "chains_ok": 0, "failures": 0}
