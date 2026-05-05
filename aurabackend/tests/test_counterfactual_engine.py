"""End-to-end tests for the Counterfactual Audit Engine.

Layered:

* ``test_run_estimators_*``         — DoWhy estimator fan-out on synthetic
* ``test_run_refuters_*``           — DoWhy refuter fan-out on synthetic
* ``test_critic_flags_*``           — adversarial critic with mocked LLM
* ``test_parser_extracts_*``        — NL parser with mocked LLM
* ``test_run_job_*``                — full engine including audit seal
* ``test_renderers_*``              — per-audience renderer dispatch
* ``test_service_*``                — FastAPI app
* ``test_gateway_proxies_*``        — gateway router → service end-to-end

All tests use the unified mock LLM (``tests/_mock_llm.py``) so the BATS
+ Audit observer chain runs in tests identically to production.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from counterfactual_service.engine import (
    dowhy_available,
    run_estimators,
    run_job,
    run_refuters,
)
from counterfactual_service.schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from tests._mock_llm import MockRule, UnifiedMockLLM, install_mock
from tests._synthetic_data import (
    TRUE_EFFECT,
    synthetic_dag_full,
    synthetic_dag_missing_confounder,
    synthetic_dataset,
)

pytestmark = pytest.mark.skipif(
    not dowhy_available(),
    reason="dowhy not installed; engine tests require requirements-causal.txt",
)


# ── Estimators ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_estimators_recover_synthetic_effect():
    df = synthetic_dataset(n=800)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("1900-01-01", "2099-01-01"))

    estimates = await run_estimators(df, treatment, outcome, synthetic_dag_full())
    methods_returned = {e.method for e in estimates}
    assert methods_returned == {"linear_regression", "ipw", "psm", "double_ml"}
    # Estimator order must be deterministic (sorted by method name).
    assert [e.method for e in estimates] == sorted(methods_returned)
    # At least 3 of 4 should succeed and recover within tolerance.
    valid = [e for e in estimates if e.error is None]
    assert len(valid) >= 3, f"too many estimator failures: {[e.error for e in estimates]}"
    close_enough = [e for e in valid if abs(e.point - TRUE_EFFECT) < 0.5]
    assert len(close_enough) >= 2, (
        f"estimators way off; expected ~{TRUE_EFFECT}, got "
        f"{[(e.method, e.point) for e in valid]}"
    )
    for e in valid:
        assert e.ci_lower <= e.ci_upper


@pytest.mark.asyncio
async def test_run_estimators_returns_one_record_per_method_on_failure():
    """Even with garbage input, each estimator gets exactly one record
    (with ``.error`` populated) — fan-out contract."""
    import pandas as pd
    df = pd.DataFrame({"treatment": [1, 2, 3], "outcome": [1, 2, 3]})  # tiny n + no confounder
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("1900-01-01", "2099-01-01"))

    estimates = await run_estimators(df, treatment, outcome, {"edges": [["treatment", "outcome"]]})
    # All four registered methods get a record (success or error)
    assert len(estimates) == 4


# ── Refuters ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_refuters_run_on_synthetic():
    df = synthetic_dataset(n=400)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("1900-01-01", "2099-01-01"))
    refuters = await run_refuters(df, treatment, outcome, synthetic_dag_full())
    refuter_names = [r.refuter for r in refuters]
    assert refuter_names == sorted({"random_common_cause", "placebo", "data_subset", "sensitivity"})
    # Each refuter is one record (either ran or returned an error) — fan-out
    # contract holds even on partial failure.
    assert len(refuters) == 4


# ── Adversarial critic ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_critic_flags_missing_confounder(monkeypatch):
    from agents.base import AgentContext
    from agents.specialists.adversarial_critic_agent import AdversarialCriticAgent

    canned = json.dumps({"challenges": [{
        "text": "DAG omits seasonality which is correlated with both treatment and outcome",
        "severity": "high",
        "suggested_check": "add seasonality as a parent of treatment and outcome",
    }]})
    install_mock(monkeypatch, UnifiedMockLLM(rules=[
        MockRule(re.compile(r"adversarial|critic|challenge", re.I), canned),
    ]))

    agent = AdversarialCriticAgent()
    ctx = AgentContext(
        user_prompt="critique counterfactual",
        task_description="Find missing confounders.",
        upstream_results={
            "estimates": [{"method": "ipw", "point": 3.2, "ci_lower": 2.8, "ci_upper": 3.6, "n_samples": 800}],
            "refutations": [{"refuter": "placebo", "passed": False}],
            "dag": {"edges": [["treatment", "outcome"]]},
            "treatment": {"column": "treatment"},
            "outcome": {"column": "outcome"},
        },
    )
    res = await agent.execute(ctx)
    assert res.succeeded, res.error
    challenges = res.output["challenges"]
    assert any(c["severity"] == "high" for c in challenges)


# ── NL parser ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parser_extracts_treatment_outcome(monkeypatch):
    from agents.base import AgentContext
    from agents.specialists.counterfactual_parser_agent import CounterfactualParserAgent

    canned = json.dumps({
        "treatment": {"column": "price_change_may", "actual": 0.08, "counterfactual": 0.0},
        "outcome": {
            "column": "monthly_revenue",
            "agg": "sum",
            "window": ["2025-07-01", "2025-09-30"],
        },
    })
    install_mock(monkeypatch, UnifiedMockLLM(rules=[
        MockRule(re.compile(r"counterfactual|parse", re.I), canned),
    ]))

    agent = CounterfactualParserAgent()
    ctx = AgentContext(
        user_prompt="What would Q3 revenue have been if we hadn't raised prices in May?",
        task_description="Parse counterfactual question.",
        schema_context={"sales_2025": ["price_change_may", "monthly_revenue", "month"]},
    )
    res = await agent.execute(ctx)
    assert res.succeeded, res.error
    out = res.output
    assert out["treatment"]["column"] == "price_change_may"
    assert out["outcome"]["agg"] == "sum"


# ── Full engine: run_job ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_job_produces_sealed_artifact(monkeypatch, tmp_path):
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=400)
    query = CounterfactualQuery(
        question="test",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="synthetic"),
    )
    artifact = await run_job(query, df=df)

    assert len(artifact.estimates) == 4
    assert len(artifact.refutations) == 4
    assert artifact.confidence in {"low", "medium", "high"}
    assert artifact.audit_record_hash and len(artifact.audit_record_hash) == 64
    assert artifact.dataset_fingerprint and len(artifact.dataset_fingerprint) == 64
    # Estimates and refutations are sorted (hash-stable contract)
    assert [e.method for e in artifact.estimates] == sorted(e.method for e in artifact.estimates)
    assert [r.refuter for r in artifact.refutations] == sorted(r.refuter for r in artifact.refutations)


@pytest.mark.asyncio
async def test_run_job_artifact_structural_stability_across_runs(monkeypatch, tmp_path):
    """Two runs with identical input produce structurally identical artifacts:
    same dataset fingerprint, same set of estimators+refuters, point estimates
    within a sanity tolerance. **Byte-identical** hash stability is Sprint 9
    scope — see spec §4.5 (critic-cache) and Risk #8 (LLM nondeterminism). At
    Sprint 8 the engine pins enough state that point estimates agree to within
    a few percent, but DoWhy's PSM/IPW have internal random sampling that
    Sprint 9 will pin via seed-from-request_hash."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=300, seed=0x1234)
    query = CounterfactualQuery(
        question="stability",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="synthetic"),
    )
    a = await run_job(query, df=df)
    b = await run_job(query, df=df)

    # Dataset fingerprint is deterministic — pure hash of the bytes.
    assert a.dataset_fingerprint == b.dataset_fingerprint

    # Same set of methods returned, in the same order.
    assert [e.method for e in a.estimates] == [e.method for e in b.estimates]
    assert [r.refuter for r in a.refutations] == [r.refuter for r in b.refutations]

    # Point estimates agree within a sanity tolerance per method.
    a_by_method = {e.method: e.point for e in a.estimates if e.error is None}
    b_by_method = {e.method: e.point for e in b.estimates if e.error is None}
    common = set(a_by_method) & set(b_by_method)
    assert common, "no method succeeded in both runs"
    for m in common:
        assert abs(a_by_method[m] - b_by_method[m]) < 0.5, (
            f"method {m} drifted: {a_by_method[m]} vs {b_by_method[m]}"
        )


# ── Renderers ─────────────────────────────────────────────────────────

def test_renderers_produce_three_views():
    from counterfactual_service.renderers import render
    from counterfactual_service.schemas import (
        CounterfactualArtifact,
        CounterfactualEstimate,
        RefutationResult,
    )

    q = CounterfactualQuery(
        question="test",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=[("t", "y")]),
        dataset=DatasetRef(source_id="ds"),
    )
    art = CounterfactualArtifact(
        record_id="ca_1",
        query=q,
        estimates=[CounterfactualEstimate(
            method="ipw", point=1.5, ci_lower=1.0, ci_upper=2.0, n_samples=100,
        )],
        refutations=[RefutationResult(refuter="placebo", passed=True)],
        challenges=[],
        confidence="high",
        schema_version="v1",
        dataset_fingerprint="abc",
        audit_record_hash="0xdead" * 8,
    )

    op = render(art, "operator")
    assert op["confidence"] == "high"
    assert "headline" in op
    assert op["audit_record_hash"] == "0xdead" * 8

    aud = render(art, "auditor")
    assert aud["estimates_full"]
    assert aud["refutations_full"]

    an = render(art, "analyst")
    assert "raw_artifact" in an


# ── Service endpoints ─────────────────────────────────────────────────

def _poll_until_done(client, url: str, max_iters: int = 120, sleep_s: float = 0.5):
    """Sync polling helper.

    The background job runs in the asyncio event loop's default executor
    (DoWhy is sync). With sync TestClient, each ``client.get()`` enters
    the loop briefly — we add a real ``time.sleep`` between calls so the
    executor's worker threads have wall-clock time to make progress."""
    import time
    s = None
    for _ in range(max_iters):
        s = client.get(url).json()
        if s.get("state") in {"succeeded", "failed"}:
            return s
        time.sleep(sleep_s)
    return s


def test_service_endpoint_roundtrip(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app, register_dataset

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))
    register_dataset("synthetic_svc", synthetic_dataset(n=300))

    payload = {
        "question": "test",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome":   {"column": "outcome", "agg": "sum",
                      "window": ["2025-01-01", "2025-12-31"]},
        "dag":       {"edges": [
            ["seasonality", "treatment"],
            ["seasonality", "outcome"],
            ["treatment", "outcome"],
        ]},
        "dataset":   {"source_id": "synthetic_svc"},
        "audience":  "operator",
    }
    with TestClient(app) as client:
        resp = client.post("/counterfactual/jobs", json=payload)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        s = _poll_until_done(client, f"/counterfactual/jobs/{job_id}")
        assert s is not None and s["state"] == "succeeded", s
        artifact = s["artifact"]
        assert artifact["record_id"].startswith("ca_")
        assert "headline" in artifact["rendered"]


def test_gateway_proxies_counterfactual(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    from counterfactual_service.main import register_dataset

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))
    register_dataset("synthetic_gw", synthetic_dataset(n=300))

    payload = {
        "question": "test",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome":   {"column": "outcome", "agg": "sum",
                      "window": ["2025-01-01", "2025-12-31"]},
        "dag":       {"edges": [
            ["seasonality", "treatment"],
            ["seasonality", "outcome"],
            ["treatment", "outcome"],
        ]},
        "dataset":   {"source_id": "synthetic_gw"},
        "audience":  "operator",
    }
    with TestClient(app) as client:
        r = client.post("/api/v1/counterfactual/jobs", json=payload)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        s = _poll_until_done(client, f"/api/v1/counterfactual/jobs/{job_id}")
        assert s is not None and s["state"] == "succeeded", s
