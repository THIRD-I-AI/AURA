"""BUG-305: shared.audit_log parsed AURA_AUDIT_ENABLED itself (exactly "true", process
environment only) while the production guard in shared.config checked pydantic's
settings.audit_enabled. A deployment with AURA_AUDIT_ENABLED=1 passed the guard and
then recorded nothing."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = (
    "from shared.config import settings; from shared import audit_log; "
    "print(int(settings.audit_enabled), int(audit_log.AUDIT_ENABLED))"
)


def _probe(value, tmp_path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "AURA_AUDIT_ENABLED"}
    if value is not None:
        env["AURA_AUDIT_ENABLED"] = value
    env["AURA_AUDIT_DIR"] = str(tmp_path / "audit")
    env["ENVIRONMENT"] = "development"
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    return out.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("value", ["1", "yes", "on", "true", "TRUE", "True"])
def test_every_spelling_the_guard_accepts_turns_the_audit_trail_on(value, tmp_path):
    assert _probe(value, tmp_path) == "1 1"


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_a_false_value_turns_it_off_for_both(value, tmp_path):
    assert _probe(value, tmp_path) == "0 0"
