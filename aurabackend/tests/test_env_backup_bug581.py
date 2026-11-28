"""BUG-581 follow-up: the SSM copy of .env was a one-time snapshot. redeploy.sh now runs
env-backup.sh on every deploy, which pushes .env to SSM only when its hash changes."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/aws-free-tier/env-backup.sh"

posix_bash = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="runs env-backup.sh under a POSIX bash (on Windows `bash` may resolve to WSL)",
)


def _setup(tmp_path, env_text="SECRET_KEY=a\n"):
    work = tmp_path / "deploy"
    work.mkdir()
    shutil.copy(SCRIPT, work / "env-backup.sh")
    if env_text is not None:
        (work / ".env").write_text(env_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "aws"
    fake.write_text('#!/usr/bin/env bash\necho "$@" >> "$FAKE_AWS_LOG"\nexit "${FAKE_AWS_RC:-0}"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return work, bin_dir


def _run(work, bin_dir, tmp_path, rc=0):
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "FAKE_AWS_LOG": str(tmp_path / "aws.log"), "FAKE_AWS_RC": str(rc)}
    return subprocess.run(["bash", str(work / "env-backup.sh")], env=env, capture_output=True, text=True, timeout=30)


def _calls(tmp_path):
    log = tmp_path / "aws.log"
    return log.read_text().splitlines() if log.exists() else []


@posix_bash
def test_pushes_once_then_only_when_the_file_changes(tmp_path):
    work, bin_dir = _setup(tmp_path)
    assert _run(work, bin_dir, tmp_path).returncode == 0
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert "ssm put-parameter" in calls[0] and "--type SecureString" in calls[0]
    assert "--value file://.env" in calls[0] and "/aura/prod/env" in calls[0]

    assert _run(work, bin_dir, tmp_path).returncode == 0
    assert len(_calls(tmp_path)) == 1  # unchanged file: no write

    (work / ".env").write_text("SECRET_KEY=b\n", encoding="utf-8")
    assert _run(work, bin_dir, tmp_path).returncode == 0
    assert len(_calls(tmp_path)) == 2


@posix_bash
def test_a_failed_push_is_reported_and_retried_next_run(tmp_path):
    work, bin_dir = _setup(tmp_path)
    r = _run(work, bin_dir, tmp_path, rc=1)
    assert r.returncode == 1 and "could not be updated" in r.stderr
    assert not (work / ".env.ssm.sha256").exists()
    assert _run(work, bin_dir, tmp_path).returncode == 0
    assert len(_calls(tmp_path)) == 2


@posix_bash
def test_no_env_file_is_not_an_error_and_calls_nothing(tmp_path):
    work, bin_dir = _setup(tmp_path, env_text=None)
    assert _run(work, bin_dir, tmp_path).returncode == 0
    assert _calls(tmp_path) == []


def test_redeploy_runs_it_without_letting_it_block_the_deploy():
    redeploy = (ROOT / "deploy/aws-free-tier/redeploy.sh").read_text(encoding="utf-8")
    assert "./env-backup.sh ||" in redeploy
