"""
Counterfactual Audit Engine — eval-gate layers.

Extends the existing 8-layer eval-gate (``test_e2e_eval_gate.py``) with
three new contractual layers:

* **Layer 9 — causal correctness.** Engine recovers a known synthetic
  treatment effect within a mean-absolute-error bound on a fully-
  specified DAG.
* **Layer 10 — re-execution byte-identity.** Two engine runs with
  identical query + dataset produce identical ``audit_record_hash``.
  Sprint 11 upgrade — previously this layer asserted only replay-via-
  persistence byte-identity; now it asserts true re-execution
  determinism via seed-from-request_hash + sequential fan-out.
* **Layer 11 — adversarial detection.** Confounded synthetic data + a
  DAG that omits the confounder → critic emits ≥ 1 high-severity
  challenge AND the engine's confidence label is not "high".

Each layer is a standalone pytest. CI runs them inside the existing
``eval-gate`` job after backend-test passes.
"""
from __future__ import annotations

import json
import re
from statistics import mean

import pytest

from counterfactual_service.engine import dowhy_available, run_job
from counterfactual_service.main import register_dataset
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
    reason="dowhy required for the counterfactual eval gate",
)


# ── Layer 9: causal correctness ───────────────────────────────────────

@pytest.mark.asyncio
async def test_layer9_engine_recovers_synthetic_effect_within_mae(monkeypatch, tmp_path):
    """The engine, given the correct DAG, recovers ``TRUE_EFFECT`` within
    MAE bound 0.30. Critic returns no challenges so the test isolates
    estimator fidelity."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=1000)
    register_dataset("synthetic_layer9", df)
    query = CounterfactualQuery(
        question="layer9",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="synthetic_layer9"),
    )

    artifact = await run_job(query, df=df)
    valid = [e for e in artifact.estimates if e.error is None]
    assert valid, (
        "no estimator produced a result: "
        f"{[(e.method, e.error) for e in artifact.estimates]}"
    )
    avg_point = mean(e.point for e in valid)
    mae = abs(avg_point - TRUE_EFFECT)
    assert mae < 0.30, (
        f"Layer 9 FAIL: engine off by MAE={mae:.3f} (cap 0.30); "
        f"avg={avg_point:.3f} vs true={TRUE_EFFECT:.3f}; "
        f"per-method: {[(e.method, round(e.point, 3)) for e in valid]}"
    )

    # The fingerprint and audit hash must be populated for a sealed artifact.
    assert artifact.dataset_fingerprint and len(artifact.dataset_fingerprint) == 64
    assert artifact.audit_record_hash and len(artifact.audit_record_hash) == 64


# ── Layer 10: re-execution byte-identity ──────────────────────────────

@pytest.mark.asyncio
async def test_layer10_two_engine_runs_produce_identical_hash(monkeypatch, tmp_path):
    """Sprint 11 contract: re-running the engine on the same logical
    input — same query, same dataset, same critic-cache state — must
    produce a byte-identical artifact_hash.

    This relies on (a) seed-from-request_hash threading into DoWhy,
    (b) sequential estimator/refuter fan-out so the seeds aren't
    trampled by concurrent threads, and (c) the critic-cache
    pinning the LLM critique. Layer 9 covers correctness; this
    layer covers reproducibility."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))

    df = synthetic_dataset(n=300, seed=0xdeadbeef)
    query = CounterfactualQuery(
        question="layer10",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="layer10"),
    )

    art_a = await run_job(query, df=df)
    art_b = await run_job(query, df=df)

    # Dataset fingerprint pinned by defensive copy
    assert art_a.dataset_fingerprint == art_b.dataset_fingerprint, (
        "Layer 10 FAIL: dataset_fingerprint drifted across runs"
    )

    # Each estimator's point + CI bounds match to canonical-JSON precision.
    est_a = sorted([
        (e.method, round(e.point, 6), round(e.ci_lower, 6), round(e.ci_upper, 6))
        for e in art_a.estimates if e.error is None
    ])
    est_b = sorted([
        (e.method, round(e.point, 6), round(e.ci_lower, 6), round(e.ci_upper, 6))
        for e in art_b.estimates if e.error is None
    ])
    assert est_a == est_b, (
        f"Layer 10 FAIL: estimator outputs drifted across runs\n  a={est_a}\n  b={est_b}"
    )

    # Final byte-identity: two runs → same audit_record_hash.
    assert art_a.audit_record_hash == art_b.audit_record_hash, (
        f"Layer 10 FAIL: audit_record_hash drifted across runs\n"
        f"  a={art_a.audit_record_hash}\n  b={art_b.audit_record_hash}"
    )


# ── Layer 11: adversarial detection ───────────────────────────────────

@pytest.mark.asyncio
async def test_layer11_critic_flags_missing_confounder(monkeypatch, tmp_path):
    """When the DAG omits the seasonality confounder, the critic — given
    the estimates and DAG — emits at least one high-severity challenge
    AND the engine's confidence label is not 'high'."""
    canned = json.dumps({"challenges": [{
        "text": (
            "DAG omits seasonality which is correlated with both "
            "treatment and outcome — estimate is biased upward."
        ),
        "severity": "high",
        "suggested_check": "add seasonality as a parent of treatment and outcome",
    }]})
    install_mock(monkeypatch, UnifiedMockLLM(rules=[
        MockRule(re.compile(r"adversarial|critic|challenge", re.I), canned),
    ]))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=600)
    register_dataset("synthetic_layer11", df)
    query = CounterfactualQuery(
        question="layer11",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_missing_confounder()["edges"]),
        dataset=DatasetRef(source_id="synthetic_layer11"),
    )

    artifact = await run_job(query, df=df)

    high_sev = [c for c in artifact.challenges if c.severity == "high"]
    assert high_sev, (
        f"Layer 11 FAIL: no high-severity challenge raised; "
        f"got {[(c.severity, c.text[:80]) for c in artifact.challenges]}"
    )
    assert artifact.confidence in {"low", "medium"}, (
        f"Layer 11 FAIL: confidence={artifact.confidence} on a "
        "missing-confounder DAG should be low or medium, not high"
    )
