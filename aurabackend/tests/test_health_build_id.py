"""BUG-204: /health reported only `version: 2.0.0`, so nobody could tell which commit production was running --
which is how a 2-week-stale deployment went unnoticed (BUG-202)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_health_reports_the_build_from_the_environment(monkeypatch):
    from fastapi.testclient import TestClient

    from shared.service_factory import create_service

    monkeypatch.setenv("AURA_GIT_SHA", "abc1234def")
    app = create_service(name="t", service_tag="t", description="t")
    assert TestClient(app).get("/health").json()["build"] == "abc1234def"


def test_health_build_defaults_to_unknown(monkeypatch):
    from fastapi.testclient import TestClient

    from shared.service_factory import create_service

    monkeypatch.delenv("AURA_GIT_SHA", raising=False)
    app = create_service(name="t", service_tag="t", description="t")
    assert TestClient(app).get("/health").json()["build"] == "unknown"


def test_every_runtime_image_bakes_in_the_commit_and_cd_passes_it():
    dockerfile = (ROOT / "aurabackend/Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("ARG GIT_SHA=unknown") == 3 and dockerfile.count("ENV AURA_GIT_SHA=${GIT_SHA}") == 3
    cd = (ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert "GIT_SHA=${{ github.sha }}" in cd


def test_the_live_ledger_row_records_the_deployed_build(tmp_path):
    spec = importlib.util.spec_from_file_location("verify_live", ROOT / "scripts/verify_live_deployment.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_live"] = mod  # dataclasses look their module up by name
    spec.loader.exec_module(mod)
    results = [
        mod.CheckResult("health", "pass", "environment=production build=0123456789abcdef"),
        mod.CheckResult("login", "pass", ""),
        mod.CheckResult("fix:bug196_dashboard_fs_locked", "pass", ""),
    ]
    ledger = tmp_path / "log.md"
    mod.append_ledger(results, "https://example", str(ledger))
    row = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "(build 0123456)" in row and "| 2/2 |" in row and "| 1/1 |" in row
