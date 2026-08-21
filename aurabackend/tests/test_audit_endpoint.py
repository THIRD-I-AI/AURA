"""Audit-your-own-data — worker + endpoint."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_demo_like_csv(path):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(7)
    n = 600
    x = rng.normal(0, 1, n)
    t = ((0.6 * x + rng.normal(0, 0.5, n)) > 0).astype(int)
    y = (0.5 + 0.4 * x - 0.6 * t + rng.normal(0, 0.3, n))
    pd.DataFrame({"flag": t, "approved": y, "score": x}).to_csv(path, index=False)


# ── Tier B: out-of-process worker produces a signed, honest artifact ─

def test_run_audit_subprocess_produces_signed_artifact_with_honesty(tmp_path, monkeypatch):
    pytest.importorskip("econml")
    monkeypatch.chdir(tmp_path)
    up = tmp_path / "data" / "uploads"
    up.mkdir(parents=True)
    _write_demo_like_csv(up / "decisions.csv")

    from counterfactual_service.audit_worker import run_audit_subprocess
    result = run_audit_subprocess({
        "uploaded_file": "decisions.csv", "treatment": "flag",
        "outcome": "approved", "confounders": ["score"], "instrument": None,
    })
    methods = {e["method"] for e in result["estimates"]}
    assert {"double_ml", "tmle"}.issubset(methods)
    assert result["audit_record_hash"]
    assert result["signature_status"] == "signed"
    assert "identification" in result and "no unmeasured confounding" in result["identification"]
    assert "sensitivity_headline" in result
    assert result["data_quality"]["n_clean"] >= 100


# ── endpoint: pre-validate + non-blocking job ───────────────────────

from fastapi.testclient import TestClient  # noqa: E402

from shared.auth import create_access_token  # noqa: E402


# /counterfactual/audit and the job poller are authenticated + tenant-scoped
# (see main._new_job) — the same token must both submit and poll.
def _auth() -> dict:
    """Mint a FRESH token on every call.

    This was a module-level constant, so the token was minted at IMPORT time --
    minute 0 of pytest collection. settings.access_token_expire_minutes defaults
    to 30, and the full suite can run far longer than that (observed: 1h42m
    under load, versus ~21m unloaded), so every test in this module that reached
    the network past minute 30 failed with AUTHENTICATION_REQUIRED. The suite
    passed when it was fast and failed when it was slow, which is why this sat
    here undetected.

    The claims are unchanged, so submit and poll still resolve to the same
    tenant -- only the mint time moves.
    """
    return {"Authorization":
            f"Bearer {create_access_token({'sub': 'audit-tester', 'org_id': 'org-audit'})}"}


def _client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    from counterfactual_service.main import app
    return TestClient(app, headers=_auth())


def test_audit_404_when_file_missing(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "nope.csv", "treatment": "t", "outcome": "y", "confounders": []})
    assert r.status_code == 404


def test_audit_400_when_column_missing(tmp_path, monkeypatch):
    import pandas as pd
    c = _client(tmp_path, monkeypatch)
    pd.DataFrame({"flag": [0, 1], "approved": [1, 0]}).to_csv(
        tmp_path / "data" / "uploads" / "d.csv", index=False)
    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "d.csv", "treatment": "flag", "outcome": "approved",
        "confounders": ["does_not_exist"]})
    assert r.status_code == 400 and "does_not_exist" in r.json()["detail"]


def test_audit_rejects_path_traversal(tmp_path, monkeypatch):
    """A traversal payload in uploaded_file must never escape the uploads dir."""
    c = _client(tmp_path, monkeypatch)
    for bad in ("../../etc/passwd", "..", "a/b.csv", "x;rm -rf"):
        r = c.post("/counterfactual/audit", json={
            "uploaded_file": bad, "treatment": "t", "outcome": "y", "confounders": []})
        assert r.status_code == 404, (bad, r.status_code)


def test_audit_wiring_creates_job_and_stores_result(tmp_path, monkeypatch):
    """Endpoint wiring: pre-validate → offload → store the worker's result. Uses a
    fast stub (engine correctness is covered by the worker test) so this is
    deterministic, not a 30s TestClient background-task race."""
    import time

    import pandas as pd

    from counterfactual_service import main as m
    c = _client(tmp_path, monkeypatch)
    pd.DataFrame({"flag": [0, 1] * 80, "approved": [1, 0] * 80, "score": [0.1] * 160}).to_csv(
        tmp_path / "data" / "uploads" / "d.csv", index=False)

    def _fast_audit(payload):
        return {
            "audit_record_hash": "stub", "estimates": [], "signature_status": "signed",
            "identification": "assumes no unmeasured confounding beyond: score",
            "sensitivity_headline": "Robustness: E-value about 1.70",
            "data_quality": {"n_input": 160, "n_clean": 160, "n_dropped": 0,
                             "treatment_is_binary": True, "warnings": []},
        }

    monkeypatch.setattr(m, "run_audit_subprocess", _fast_audit)
    monkeypatch.setattr(m, "get_audit_pool", lambda: None)  # default thread executor

    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "d.csv", "treatment": "flag", "outcome": "approved",
        "confounders": ["score"]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    art = None
    for _ in range(40):
        jr = c.get(f"/counterfactual/jobs/{job_id}").json()
        if jr["state"] in ("succeeded", "failed"):
            art = jr
            break
        time.sleep(0.25)
    assert art is not None and art["state"] == "succeeded", art
    assert art["artifact"]["sensitivity_headline"]
    assert art["artifact"]["data_quality"]["n_clean"] == 160


def test_audit_reachable_through_gateway(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    from api_gateway.main import app as gw
    gc = TestClient(gw, headers=_auth())
    r = gc.post("/api/v1/counterfactual/audit", json={
        "uploaded_file": "nope.csv", "treatment": "t", "outcome": "y", "confounders": []})
    assert r.status_code == 404  # routed through; file-missing check fired


def test_audit_through_gateway_requires_auth():
    """The gateway calls the service handler directly (in-process mount), so
    the service's own Depends() never resolves — the dependency has to be
    declared on the gateway route. Assert it actually is."""
    from api_gateway.main import app as gw
    r = TestClient(gw).post("/api/v1/counterfactual/audit", json={
        "uploaded_file": "nope.csv", "treatment": "t", "outcome": "y", "confounders": []})
    assert r.status_code == 401
