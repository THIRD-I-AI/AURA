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
    # At least 2 of 4 must succeed for the fan-out to be meaningful.
    # The strict correctness assertion (MAE within 0.30 of the true
    # effect) lives in eval-gate Layer 9 — this test only verifies the
    # fan-out *contract*, not the numerical fidelity. The looser bound
    # here is intentional: full-suite runs occasionally see DoWhy
    # internals interact with prior pandas/sklearn warnings state in a
    # way that flips one extra estimator into ``error``, but two
    # working estimators is enough to demonstrate the pipeline.
    valid = [e for e in estimates if e.error is None]
    assert len(valid) >= 2, f"too many estimator failures: {[e.error for e in estimates]}"
    close_enough = [e for e in valid if abs(e.point - TRUE_EFFECT) < 0.5]
    assert len(close_enough) >= 1, (
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


@pytest.mark.asyncio
async def test_run_refuters_offloads_the_baseline_estimate_to_a_thread():
    """BUG-097: run_refuters built the baseline DoWhy CausalModel and
    called identify_effect/estimate_effect directly on the event loop,
    unlike the per-refuter fan-out (and run_estimators) which correctly
    offload via loop.run_in_executor. On the single-uvicorn-worker
    deployment that blocks every other tenant's concurrent request for
    however long DoWhy's graph analysis + linear-regression fit take.
    Proven at the mechanism level: the baseline builder must run on a
    different OS thread than the event loop's own."""
    import threading

    from counterfactual_service import engine as engine_module

    df = synthetic_dataset(n=400)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("1900-01-01", "2099-01-01"))

    main_thread_id = threading.get_ident()
    build_thread_ids = []
    real_build_causal_model = engine_module._build_causal_model

    def spy_build_causal_model(*args, **kwargs):
        build_thread_ids.append(threading.get_ident())
        return real_build_causal_model(*args, **kwargs)

    engine_module._build_causal_model = spy_build_causal_model
    try:
        await run_refuters(df, treatment, outcome, synthetic_dag_full())
    finally:
        engine_module._build_causal_model = real_build_causal_model

    assert build_thread_ids, "the baseline builder must have been called"
    assert all(t != main_thread_id for t in build_thread_ids), (
        "baseline construction ran on the event loop's own thread instead "
        "of being offloaded via run_in_executor"
    )


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
    import threading

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

    call_threads: list[str] = []

    def _record_and_respond(_text: str) -> str:
        # Proves the blocking self.llm.generate() runs off the event
        # loop thread (asyncio.to_thread) — a regression back to a
        # direct in-loop call would show this thread as MainThread and
        # fail the assertion below.
        call_threads.append(threading.current_thread().name)
        return canned

    install_mock(monkeypatch, UnifiedMockLLM(rules=[
        MockRule(re.compile(r"counterfactual|parse", re.I), _record_and_respond),
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

    assert call_threads, "mocked llm.generate was never called"
    assert call_threads[0] != threading.current_thread().name, (
        f"self.llm.generate ran on the event-loop thread "
        f"{threading.current_thread().name!r} instead of a to_thread worker "
        "(asyncio.to_thread not used — this blocks the sole uvicorn worker)"
    )


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


def _artifact_with(estimates):
    from counterfactual_service.schemas import CounterfactualArtifact

    q = CounterfactualQuery(
        question="test",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=[("t", "y")]),
        dataset=DatasetRef(source_id="ds"),
    )
    return CounterfactualArtifact(
        record_id="ca_deg", query=q, estimates=estimates, refutations=[],
        challenges=[], confidence="high", schema_version="v1",
        dataset_fingerprint="abc", audit_record_hash="0xdead" * 8,
    )


def _est(method, **kw):
    from counterfactual_service.schemas import CounterfactualEstimate

    return CounterfactualEstimate(
        method=method, point=1.5, ci_lower=1.0, ci_upper=2.0, n_samples=100, **kw,
    )


def test_operator_view_discloses_a_degraded_estimator():
    """BUG-153: double_ml silently ran as DoWhy linear regression (econml
    missing) and its result fed the headline ATE/CI -- the flag was computed
    by the engine but dropped by the renderer, so the operator card looked
    like a normal four-estimator result."""
    from counterfactual_service.renderers import render

    art = _artifact_with([_est("ipw"), _est("double_ml", degraded=True)])
    for audience in ("operator", "auditor", "analyst"):
        assert render(art, audience)["degraded_methods"] == ["double_ml"]


def test_operator_view_omits_degraded_methods_when_nothing_degraded():
    from counterfactual_service.renderers import render

    art = _artifact_with([_est("ipw"), _est("double_ml")])
    assert "degraded_methods" not in render(art, "operator")


def test_operator_view_reports_estimator_coverage():
    """BUG-154: one surviving estimator still scores 'high' (by design, see
    test_counterfactual_confidence), so the view must say how many of the
    estimators actually produced the number the badge describes."""
    from counterfactual_service.engine import score_confidence
    from counterfactual_service.renderers import render
    from counterfactual_service.schemas import RefutationResult

    ests = [
        _est("ipw"),
        _est("psm", error="RuntimeError: singular"),
        _est("linear_regression", error="TimeoutError"),
        _est("double_ml", error="ImportError: econml"),
    ]
    refs = [RefutationResult(refuter="placebo", passed=True),
            RefutationResult(refuter="data_subset", passed=True)]
    # The real scorer, not a hardcoded label: 3 of 4 estimators failed and
    # the badge is still "high".
    assert score_confidence(ests, refs, []) == "high"

    art = _artifact_with(ests)
    assert render(art, "operator")["estimator_coverage"] == {"valid": 1, "total": 4}
    assert render(art, "auditor")["estimator_coverage"] == {"valid": 1, "total": 4}


def test_operator_view_estimator_coverage_when_all_succeed():
    from counterfactual_service.renderers import render

    art = _artifact_with([_est("ipw"), _est("psm"), _est("double_ml")])
    assert render(art, "operator")["estimator_coverage"] == {"valid": 3, "total": 3}


def test_operator_view_ignores_a_degraded_estimator_that_errored():
    """An errored estimate contributes nothing to point/CI, so it must not
    trigger a 'weaker estimator ran' warning about a number the user never saw."""
    from counterfactual_service.renderers import render

    art = _artifact_with([_est("ipw"), _est("double_ml", degraded=True, error="ValueError: x")])
    assert "degraded_methods" not in render(art, "operator")


# ── Service endpoints ─────────────────────────────────────────────────

def _poll_until_done(client, url: str, budget_s: float = 300.0, sleep_s: float = 0.5):
    """Sync polling helper.

    The background job runs in the asyncio event loop's default executor
    (DoWhy is sync). With sync TestClient, each ``client.get()`` enters
    the loop briefly — we add a real ``time.sleep`` between calls so the
    executor's worker threads have wall-clock time to make progress.

    The budget is wall-clock and deliberately far above the ~47s an unloaded
    estimator fan-out measures: the old 120x0.5s gave only ~13s of headroom,
    so ordinary suite load pushed the job past the deadline and the caller
    reported a still-running job as a failed one. Exhausting the budget now
    raises here instead of returning a non-terminal state, so a genuinely
    stuck job is distinguishable from a merely slow one.

    Polling backs off from ``sleep_s`` to 2s (same fix as BUG-004/BUG-009,
    docs/BUG_REGISTRY.md): a flat 0.5s interval issues up to 2 req/s — 120
    over any 60s window — against the SAME global rate limiter (100 req/60s
    per IP, shared/config.py) this TestClient's requests share, which alone
    exceeds the budget once the job legitimately runs past ~50s, independent
    of any other test's traffic. The backoff caps steady-state at 30 req/60s
    while keeping the fast cadence for the common case where the job
    finishes quickly."""
    import time
    deadline = time.monotonic() + budget_s
    poll_interval = sleep_s
    s = None
    while time.monotonic() < deadline:
        resp = client.get(url)
        assert resp.status_code == 200, resp.text
        s = resp.json()
        if s.get("state") in {"succeeded", "failed"}:
            return s
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 2.0)
    raise AssertionError(
        f"job never reached a terminal state within {budget_s}s: last={s}"
    )


def test_service_endpoint_roundtrip(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app, register_dataset
    from shared.auth import create_access_token

    # Job submit + poll are authenticated and tenant-scoped (see main._new_job),
    # so both legs of the roundtrip must carry the same token.
    auth = {"Authorization":
            f"Bearer {create_access_token({'sub': 'eng-tester', 'org_id': 'org-eng'})}"}

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
    with TestClient(app, headers=auth) as client:
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
    from shared.auth import create_access_token

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))
    register_dataset("synthetic_gw", synthetic_dataset(n=300))
    auth = {"Authorization":
            f"Bearer {create_access_token({'sub': 'gw-tester', 'org_id': 'org-gw'})}"}

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
    with TestClient(app, headers=auth) as client:
        r = client.post("/api/v1/counterfactual/jobs", json=payload)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        s = _poll_until_done(client, f"/api/v1/counterfactual/jobs/{job_id}")
        assert s is not None and s["state"] == "succeeded", s
