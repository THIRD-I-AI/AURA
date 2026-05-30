"""S31b — /demo endpoint tests."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from counterfactual_service import main as m
from counterfactual_service.main import app

client = TestClient(app)
DEMO_METHODS = {"double_ml", "tmle", "iv"}


# ── Tier A: endpoint wiring (no real audit) ─────────────────────────

def test_list_demo_scenarios():
    r = client.get("/counterfactual/demo/scenarios")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["scenarios"]]
    assert "fair_lending" in ids


def test_unknown_scenario_404():
    r = client.post("/counterfactual/demo/does_not_exist")
    assert r.status_code == 404


def test_demo_cold_cache_returns_503_not_blocking_audit(monkeypatch):
    """With no pre-warmed artifact and no ?fresh, the endpoint must refuse
    (503) rather than launch a GIL-bound audit that would freeze the
    in-process gateway."""
    monkeypatch.setattr(m, "_demo_last_good", {})
    r = client.post("/counterfactual/demo/fair_lending")
    assert r.status_code == 503
    assert "warm_demos" in r.json()["detail"]


def test_demo_reachable_through_gateway():
    """The gateway proxies /api/v1/counterfactual/demo/* — the paths S31a's
    frontend builds against."""
    from api_gateway.main import app as gateway_app
    gc = TestClient(gateway_app)
    r = gc.get("/api/v1/counterfactual/demo/scenarios")
    assert r.status_code == 200
    assert any(s["id"] == "fair_lending" for s in r.json()["scenarios"])


def test_demo_serves_prewarmed_artifact_instantly(monkeypatch):
    """When a scenario is pre-warmed, POST returns the sealed artifact as an
    already-complete job — no waiting on the live fan-out."""
    fake = {
        "audit_record_hash": "deadbeefcafe",
        "estimates": [{"method": "iv", "point": -0.23, "error": None}],
        "signature_status": "signed",
        "signing_key_source": "persisted_file",
    }
    monkeypatch.setitem(m._demo_last_good, "fair_lending", fake)
    r = client.post("/counterfactual/demo/fair_lending")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True and body["scenario_id"] == "fair_lending"
    jr = client.get(f"/counterfactual/jobs/{body['job_id']}").json()
    assert jr["state"] == "succeeded"
    assert jr["artifact"]["audit_record_hash"] == "deadbeefcafe"


# ── Tier B: the real audit (needs econml + dowhy) ───────────────────

def test_audit_estimators_agree_and_iv_valid():
    pytest.importorskip("econml")
    import asyncio

    from counterfactual_service.demo_scenarios import get_scenario
    from counterfactual_service.engine import run_estimators
    sc = get_scenario("fair_lending")
    q = sc.query()
    est = asyncio.run(run_estimators(
        sc.build_dataset(), q.treatment, q.outcome, q.dag.model_dump(),
        methods=list(DEMO_METHODS), request_hash="t",
    ))
    by = {e.method: e for e in est}
    assert DEMO_METHODS.issubset(by)
    # The flag lowers approval — every estimator agrees on direction.
    for meth in DEMO_METHODS:
        assert by[meth].error is None, by[meth].error
        assert by[meth].point < 0, f"{meth} expected negative effect, got {by[meth].point}"
    # IV is the rigorous (officer-leniency) estimate; CI brackets the point.
    iv = by["iv"]
    assert iv.ci_lower < iv.point < iv.ci_upper


def test_pdf_renders_with_verdict_and_attestation():
    pytest.importorskip("reportlab")
    from counterfactual_service import pdf_renderer
    art = {
        "record_id": "ca_demo", "confidence": "high",
        "audit_record_hash": "abc123def4567890", "dataset_fingerprint": "fp",
        "schema_version": "v1",
        "signature_status": "signed", "signing_key_source": "persisted_file",
        "query": {
            "question": "did the high-risk flag lower approval?",
            "treatment": {"column": "flagged_high_risk", "actual": 1.0, "counterfactual": 0.0},
            "outcome": {"column": "approved", "agg": "mean", "window": ["1970-01-01", "2100-01-01"]},
        },
        "estimates": [
            {"method": "double_ml", "point": -0.39, "ci_lower": -0.45, "ci_upper": -0.33, "n_samples": 800, "error": None},
            {"method": "tmle", "point": -0.40, "ci_lower": -0.46, "ci_upper": -0.34, "n_samples": 800, "error": None},
            {"method": "iv", "point": -0.23, "ci_lower": -0.35, "ci_upper": -0.11, "n_samples": 800, "error": None},
        ],
        "refutations": [], "challenges": [],
    }
    pdf = pdf_renderer.render_pdf(art)
    assert pdf is not None and pdf[:4] == b"%PDF"
    assert len(pdf) > 1500


def test_pdf_renders_from_persisted_string_numbers():
    """The persistence layer serialises numeric fields as STRINGS (canonical
    JSON for byte-stable replay). report.pdf reads the persisted artifact, so
    the renderer must coerce — regression for the report.pdf 500 the live demo
    exposed."""
    pytest.importorskip("reportlab")
    from counterfactual_service import pdf_renderer
    art = {
        "record_id": "ca_demo", "confidence": "medium",
        "audit_record_hash": "abc123def4567890",
        "dataset_fingerprint": "fp", "schema_version": "v1",
        "signature_status": "signed", "signing_key_source": "persisted_file",
        "query": {
            "question": "q?",
            "treatment": {"column": "flagged_high_risk", "actual": 1.0, "counterfactual": 0.0},
            "outcome": {"column": "approved", "agg": "mean", "window": ["1970-01-01", "2100-01-01"]},
        },
        # Numbers as strings, exactly as persistence.read_artifact returns them.
        "estimates": [
            {"method": "double_ml", "point": "-0.393147", "ci_lower": "-0.45", "ci_upper": "-0.33", "n_samples": 800, "error": None},
            {"method": "iv", "point": "-0.232242", "ci_lower": "-0.35", "ci_upper": "-0.11", "n_samples": 800, "error": None},
        ],
        "refutations": [
            {"refuter": "placebo", "estimate_after": "0.000000", "p_value": "0.000000", "passed": True, "error": None},
            {"refuter": "sensitivity", "estimate_after": "-0.183501", "p_value": None, "passed": True, "error": None},
        ],
        "challenges": [],
    }
    pdf = pdf_renderer.render_pdf(art)
    assert pdf is not None and pdf[:4] == b"%PDF"
    assert len(pdf) > 1500


def test_full_audit_is_hashed_and_signed():
    pytest.importorskip("econml")
    pytest.importorskip("dowhy")
    import asyncio

    from counterfactual_service.demo_scenarios import get_scenario
    from counterfactual_service.engine import run_job
    sc = get_scenario("fair_lending")
    art = asyncio.run(run_job(sc.query(), df=sc.build_dataset(), methods=m._DEMO_METHODS))
    methods = {e.method for e in art.estimates}
    assert DEMO_METHODS.issubset(methods)
    assert art.audit_record_hash
    assert art.signature_status == "signed"
    assert art.signing_key_source in ("persisted_file", "env_hex", "env_pem", "ephemeral")
