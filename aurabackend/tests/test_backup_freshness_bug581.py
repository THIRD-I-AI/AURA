"""BUG-581: production backups failed every night for 58 days. A root run had left backups/
root-owned, the ec2-user cron could not write there, and nothing but backup.log knew.

backup.sh now refuses root and records every backup run on the data volume; the gateway
reports that record as one word in /health's ops.backup, beside checks and never gating;
and the live check backup_fresh fails unless the word is ok."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
BACKUP_SH = ROOT / "deploy/aws-free-tier/backup.sh"


def _record(status_file: Path, status: str, hours_ago: float, **extra) -> str:
    """Write a status record the way backup.sh does; return its finished_at."""
    finished = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"status": status, "finished_at": finished, "stamp": "20261123T031500Z",
              "size": "22M", "offsite": True, "error": None, **extra}
    status_file.write_text(json.dumps(record), encoding="utf-8")
    return finished


# ── the gateway's reading of the record ─────────────────────────────────────

@pytest.mark.parametrize("record, expected", [
    (("ok", 1), "ok"),
    (("ok", 25.5), "ok"),      # a slow night is still inside the 26h window
    (("ok", 27), "stale"),
    (("failed", 1), "failed"),
    (None, "missing"),
])
def test_backup_state_from_a_temp_data_dir(tmp_path, record, expected):
    from api_gateway.main import backup_state

    status_file = tmp_path / "state" / "backup_status.json"
    status_file.parent.mkdir()
    if record:
        _record(status_file, *record)
    assert backup_state(str(status_file)) == expected


def test_an_unreadable_record_is_failed_and_an_ok_without_a_time_is_stale(tmp_path):
    from api_gateway.main import backup_state

    status_file = tmp_path / "backup_status.json"
    status_file.write_text('{"status": "ok", "finished_', encoding="utf-8")
    assert backup_state(str(status_file)) == "failed"
    status_file.write_text('["ok"]', encoding="utf-8")
    assert backup_state(str(status_file)) == "failed"
    status_file.write_text('{"status": "ok", "finished_at": "last night"}', encoding="utf-8")
    assert backup_state(str(status_file)) == "stale"


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    """The real gateway app, its backup record pointed at a temp data dir."""
    import api_gateway.main as gw

    status_file = tmp_path / "backup_status.json"
    monkeypatch.setattr(gw, "BACKUP_STATUS_FILE", str(status_file))
    return TestClient(gw.app), status_file


@pytest.mark.parametrize("status, hours_ago, expected", [("ok", 27, "stale"), ("failed", 1, "failed")])
def test_a_bad_backup_never_changes_the_health_status_or_code(gateway, status, hours_ago, expected):
    client, status_file = gateway
    finished = _record(status_file, status, hours_ago, error="cannot create /opt/aura/deploy/aws-free-tier/backups/x")
    r = client.get("/health")
    # The compose healthcheck reads the HTTP code: a stale backup must not restart the gateway.
    assert r.status_code == 200 and r.json()["status"] == "healthy"
    assert r.json()["ops"] == {"backup": expected}
    # Not a check: the workbench and system-pulse UIs draw every checks entry as a service node.
    assert "backup" not in r.json()["checks"]
    # /health is public: the word only, never the record's time, stamp, path or error text.
    for leak in (finished, "20261123T031500Z", "/opt/aura", str(status_file), "backup_status"):
        assert leak not in r.text


def _service(**probes):
    from shared.service_factory import create_service

    return TestClient(create_service(name="t", service_tag="t", **probes))


def test_ops_checks_report_their_word_beside_checks_and_never_gate():
    async def up():
        return None

    async def down():
        return "db unreachable"

    async def stale():
        return "stale"

    async def crash():
        raise OSError("[Errno 5] Input/output error: '/data/state/backup_status.json'")

    r = _service(health_checks={"db": up}, ops_checks={"backup": stale}).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"
    assert r.json()["checks"] == {"db": "ok"} and r.json()["ops"] == {"backup": "stale"}
    # A failing gating check still gates, and the backup word is still reported.
    r = _service(health_checks={"db": down}, ops_checks={"backup": stale}).get("/health")
    assert r.status_code == 503 and r.json()["checks"] == {"db": "failing"}
    assert r.json()["ops"] == {"backup": "stale"}
    # With no gating checks there is no checks field, as before.
    r = _service(ops_checks={"backup": stale}).get("/health")
    assert r.status_code == 200 and r.json()["ops"] == {"backup": "stale"} and "checks" not in r.json()
    # A crashing ops probe neither takes /health down nor leaks its message.
    r = _service(health_checks={"db": up}, ops_checks={"backup": crash}).get("/health")
    assert r.status_code == 200 and r.json()["ops"] == {"backup": "unknown"}
    assert "Input/output" not in r.text and "/data/state" not in r.text


# ── the live check ──────────────────────────────────────────────────────────

def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_live", ROOT / "scripts/verify_live_deployment.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_live"] = mod  # dataclasses look their module up by name
    spec.loader.exec_module(mod)
    return mod


def _verifier_on(client):
    mod = _load_verify()
    v = mod.Verifier(base_url="http://testserver", email="e", password="p")
    v.client = client
    return mod, v


def test_backup_fresh_passes_only_on_ok_and_names_the_state_it_got(gateway):
    client, status_file = gateway
    mod, v = _verifier_on(client)
    with pytest.raises(AssertionError, match="backup is missing"):
        mod.check_backup_fresh(v)
    _record(status_file, "failed", 1)
    with pytest.raises(AssertionError, match="backup is failed: the last backup run failed"):
        mod.check_backup_fresh(v)
    _record(status_file, "ok", 27)
    mod.PACE_SECONDS = 0
    result = v.run("backup_fresh", lambda: mod.check_backup_fresh(v))
    assert result.status == "fail" and "backup is stale" in result.detail
    _record(status_file, "ok", 1)
    assert "within 26h" in mod.check_backup_fresh(v)


def test_backup_fresh_fails_on_a_build_that_does_not_report_backups():
    async def up():
        return None

    mod, v = _verifier_on(_service(health_checks={"db": up}))
    with pytest.raises(AssertionError, match="before BUG-581"):
        mod.check_backup_fresh(v)
    assert ("backup_fresh", mod.check_backup_fresh) in mod.CHECKS


# ── backup.sh ───────────────────────────────────────────────────────────────

def test_backup_sh_writes_the_file_the_gateway_reads():
    import api_gateway.main as gw

    m = re.search(r'^STATUS_FILE="([^"]+)"', BACKUP_SH.read_text(encoding="utf-8"), re.M)
    assert m, "STATUS_FILE not found in backup.sh"
    assert m.group(1) == gw.BACKUP_STATUS_FILE


def test_the_record_backup_sh_writes_is_one_the_gateway_reads(tmp_path):
    """Runs write_status's own Python (lifted from backup.sh) against the gateway's reader."""
    from api_gateway.main import backup_state

    m = re.search(r"^write_status\(\) \{.*?python3 -c '(.*?)'", BACKUP_SH.read_text(encoding="utf-8"), re.M | re.S)
    assert m, "write_status's python3 -c block not found in backup.sh"
    status_file = tmp_path / "backup_status.json"
    for args, expected in ((["ok", "20261123T031500Z", "22M", "true", ""], "ok"),
                           (["failed", "20261123T031500Z", "", "false", "s3 sync failed"], "failed")):
        subprocess.run([sys.executable, "-c", m.group(1), str(status_file), *args], check=True, timeout=60)
        assert backup_state(str(status_file)) == expected
    record = json.loads(status_file.read_text(encoding="utf-8"))
    # Everything it holds; nothing secret.
    assert set(record) == {"status", "finished_at", "stamp", "size", "offsite", "error"}
    assert record["offsite"] is False and record["size"] is None and record["error"] == "s3 sync failed"
    assert not (tmp_path / "backup_status.json.tmp").exists()


posix_bash = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="runs backup.sh under a POSIX bash (on Windows `bash` may resolve to WSL)",
)

# Stand-ins on PATH. `id -u` answers $FAKE_UID. `docker` logs each call; `compose ps -q`
# answers $FAKE_CID (empty: the stack is down); `compose exec -T api_gateway python3 -c CODE
# PATH ...` runs CODE here with /data mapped to $FAKE_DATA; every other exec fails, the way
# a broken container would, so the backup stops at its first step.
FAKE_ID = '#!/bin/sh\nif [ "$1" = -u ]; then echo "$FAKE_UID"; else echo tester; fi\n'
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
[ "$1" = compose ] || exit 1
case "$2" in
  ps) [ -n "$FAKE_CID" ] && echo "$FAKE_CID"; exit 0 ;;
  exec)
    if [ "$5" = python3 ] && [ "$6" = -c ]; then
      code="$7"; path="$8"; shift 8
      exec python3 -c "$code" "$FAKE_DATA${path#/data}" "$@"
    fi
    exit 1 ;;
esac
exit 1
"""


def _run_backup(tmp_path, uid, cid="", **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("id", FAKE_ID), ("docker", FAKE_DOCKER)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    run_env = {k: val for k, val in os.environ.items() if not k.startswith("AURA_")}
    run_env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FAKE_UID=str(uid), FAKE_CID=cid,
        FAKE_DATA=str(tmp_path / "data"), FAKE_LOG=str(tmp_path / "docker.log"),
        AURA_BACKUP_ROOT=str(tmp_path / "backups"), **env,
    )
    return subprocess.run(["bash", str(BACKUP_SH)], cwd=tmp_path, env=run_env,
                          capture_output=True, text=True, timeout=60)


@posix_bash
def test_backup_refuses_root_and_touches_nothing(tmp_path):
    r = _run_backup(tmp_path, uid=0, cid="c0ffee")
    assert r.returncode == 1
    assert "refusing to run as root" in r.stderr and "ec2-user cron can no longer write" in r.stderr
    assert not (tmp_path / "backups").exists() and not (tmp_path / "docker.log").exists()
    # A refused run is not a failed backup: the last real result stays on the volume.
    assert not (tmp_path / "data/state/backup_status.json").exists()


@posix_bash
def test_the_root_override_gets_past_the_guard(tmp_path):
    r = _run_backup(tmp_path, uid=0, AURA_BACKUP_ALLOW_ROOT="1")
    assert r.returncode == 1 and "refusing to run as root" not in r.stderr
    assert "api_gateway is not running" in r.stderr  # the next check: the stand-in stack is down


@posix_bash
def test_a_failed_backup_is_recorded_on_the_volume(tmp_path):
    r = _run_backup(tmp_path, uid=1000, cid="c0ffee")
    assert r.returncode == 1 and "FAILED: could not create staging dir" in r.stderr
    record = json.loads((tmp_path / "data/state/backup_status.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["error"] == "could not create staging dir"
    assert record["offsite"] is False and record["size"] is None
    assert re.fullmatch(r"\d{8}T\d{6}Z", record["stamp"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["finished_at"])


@posix_bash
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_backup_root_fails_fast_naming_its_owner(tmp_path):
    import pwd

    backups = tmp_path / "backups"
    backups.mkdir()
    backups.chmod(0o555)
    try:
        r = _run_backup(tmp_path, uid=1000, cid="c0ffee")
    finally:
        backups.chmod(0o755)
    assert r.returncode == 1
    assert f"{backups} is owned by {pwd.getpwuid(os.getuid()).pw_name} and not writable" in r.stderr
    record = json.loads((tmp_path / "data/state/backup_status.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and str(backups) in record["error"]
