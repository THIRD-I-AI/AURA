"""A deployment can switch off the route families it does not use."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from api_gateway.main import OPTIONAL_ROUTERS, disabled_routers

_BACKEND = Path(__file__).resolve().parents[1]

_COUNT_ROUTES = """
import logging, sys, warnings
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from api_gateway.main import app
paths = [r.path for r in app.routes]
for family in ("agent", "approvals", "evolution", "synthetic", "chat", "files"):
    print(family, sum(p.startswith(f"/api/v1/{family}") for p in paths))
"""


def _route_counts(disabled: str) -> dict:
    env = {**os.environ, "AURA_DISABLED_ROUTERS": disabled, "PYTHONPATH": str(_BACKEND)}
    out = subprocess.run([sys.executable, "-c", _COUNT_ROUTES], cwd=_BACKEND, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return {name: int(n) for name, n in (line.split() for line in out.stdout.strip().splitlines())}


def test_nothing_is_switched_off_by_default():
    assert disabled_routers("") == frozenset()
    counts = _route_counts("")
    assert all(counts[name] > 0 for name in OPTIONAL_ROUTERS), counts


def test_a_switched_off_family_has_no_routes_and_the_rest_is_untouched():
    counts = _route_counts("agent, approvals,evolution,SYNTHETIC")
    assert [counts[name] for name in sorted(OPTIONAL_ROUTERS)] == [0, 0, 0, 0], counts
    assert counts["chat"] > 0 and counts["files"] > 0


def test_an_unknown_name_is_an_error_not_a_silent_no_op():
    with pytest.raises(ValueError, match="unknown router"):
        disabled_routers("agent,agnet")


def test_the_free_tier_stack_switches_off_the_families_it_has_no_use_for():
    compose = (_BACKEND.parent / "deploy" / "aws-free-tier" / "docker-compose.yml").read_text(encoding="utf-8")
    line = next(ln for ln in compose.splitlines() if ln.strip().startswith("AURA_DISABLED_ROUTERS:"))
    assert disabled_routers(line.split(":", 1)[1].strip().strip('"')) == OPTIONAL_ROUTERS
