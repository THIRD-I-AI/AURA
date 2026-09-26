"""BUG-203: the nightly backup cron entry is `./backup.sh`, but the script was committed non-executable
(mode 100644), so every run failed with 'Permission denied' and production had no automated backups for
weeks. redeploy.sh had the same mode and only worked on the box because someone chmod'ed it by hand."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ["backup.sh", "redeploy.sh", "bootstrap.sh"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", SCRIPTS)
def test_deploy_scripts_are_committed_executable(name):
    rel = f"deploy/aws-free-tier/{name}"
    out = subprocess.run(["git", "ls-files", "-s", rel], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert out, f"{rel} is not tracked by git"
    mode = out.split()[0]
    assert mode == "100755", f"{rel} is committed with mode {mode}; cron runs it as ./{name}, so it must be 100755"


def test_the_cron_lines_in_the_readme_call_scripts_that_exist():
    readme = (ROOT / "deploy/aws-free-tier/README.md").read_text(encoding="utf-8")
    for name in ("backup.sh", "redeploy.sh"):
        assert name in readme
        assert (ROOT / "deploy/aws-free-tier" / name).exists()
