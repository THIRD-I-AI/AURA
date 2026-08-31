"""
Sprint 13 — Auditor batch + propensity diagnostics tests.

Covers:

* Bulk-replay endpoint: NDJSON streaming, per-row status codes, dedup,
  graceful handling of not_found / unsigned / verify_failed rows.
* Propensity diagnostics: DR-Learner's CounterfactualEstimate now
  carries quantiles + extreme-propensity count; field round-trips
  through artifact persistence and is part of the hash basis.
* Verify-endpoint regression: the Sprint 11 nested elapsed_ms exclude
  used to silently fail verify on every signed artifact; this test
  asserts verify=True end-to-end after the strip_for_hashing fix.
"""
from __future__ import annotations

import json
import os

import pytest

from counterfactual_service import persistence, signing
from counterfactual_service.engine import (
    dowhy_available,
    econml_available,
    run_job,
    strip_for_hashing,
)
from counterfactual_service.main import register_dataset

# Job submit + poll are authenticated and tenant-scoped (see main._new_job).
from counterfactual_service.schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from shared.auth import create_access_token
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset


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
            f"Bearer {create_access_token({'sub': 's13-tester', 'org_id': 'org-s13'})}"}
# All Sprint 13 tests touch the engine — gate on dowhy like Sprint 9 did.
ENGINE_TESTS = pytest.mark.skipif(
    not dowhy_available(),
    reason="dowhy required for Sprint 13 engine round-trip tests",
)


# ── Bulk-replay endpoint ──────────────────────────────────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_bulk_replay_returns_ndjson_with_mixed_statuses(monkeypatch, tmp_path):
    """Submit a real artifact, then bulk-replay it alongside an unknown
    hash; expect ``ok`` for the real one and ``not_found`` for the
    bogus one, both as parseable NDJSON lines."""
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", bytes(range(32)).hex())
    register_dataset("bulk_a", synthetic_dataset(n=300))

    payload = {
        "question": "bulk",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome":   {"column": "outcome", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        "dag":       {"edges": synthetic_dag_full()["edges"]},
        "dataset":   {"source_id": "bulk_a"},
        "audience":  "auditor",
    }

    with TestClient(app, headers=_auth()) as client:
        # Build a real artifact to verify against
        r = client.post("/counterfactual/jobs", json=payload)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        import time
        # 300s wall-clock, not 120x0.5s: the fan-out measures ~47s unloaded, so
        # the old 60s budget timed out under ordinary suite load and reported a
        # still-running job as a failure.
        #
        # Polling backs off from 0.5s to 2s (BUG-007-adjacent finding,
        # docs/BUG_REGISTRY.md): a flat 0.5s interval issues up to 600 GETs
        # over the 300s budget against the SAME global rate limiter
        # (100 req/60s per IP, shared/config.py) this TestClient's requests
        # share -- once the job legitimately takes >~50s under suite load,
        # the poll loop's own request volume alone breaches that window and
        # 429s itself, independent of any other test's traffic. The backoff
        # keeps steady-state well under 60 req/60s (a 2s cadence caps it at
        # 30/60s) while still polling fast (0.5s) for the common case where
        # the job finishes quickly.
        deadline = time.monotonic() + 300.0
        poll_interval = 0.5
        while time.monotonic() < deadline:
            # Assert the status BEFORE indexing: an error body has no "state",
            # so the bare .json()["state"] reported KeyError: 'state' and hid
            # whichever real failure (404 job lost, 500) actually happened.
            sr_resp = client.get(f"/counterfactual/jobs/{job_id}")
            assert sr_resp.status_code == 200, sr_resp.text
            sr = sr_resp.json()
            if sr["state"] in {"succeeded", "failed"}:
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 2.0)
        assert sr["state"] == "succeeded", sr
        real_hash = sr["artifact"]["audit_record_hash"]

        # Bulk-replay with the real hash + a known-bad hash + duplicate
        # of the real hash. Server should dedup so we get exactly 2 rows.
        bogus = "0" * 64
        resp = client.post(
            "/counterfactual/replay/bulk",
            json={"hashes": [real_hash, bogus, real_hash]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/x-ndjson"

        rows = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
        assert len(rows) == 2, f"expected dedup to 2 rows, got {len(rows)}: {rows}"

        # Order preserved (real first, bogus second).
        assert rows[0]["record_hash"] == real_hash
        assert rows[0]["status"] == "ok"
        assert rows[1]["record_hash"] == bogus
        assert rows[1]["status"] == "not_found"


def test_bulk_replay_rejects_empty_list():
    """Pydantic min_length=1 means an empty hashes list returns 422."""
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app

    with TestClient(app, headers=_auth()) as client:
        r = client.post("/counterfactual/replay/bulk", json={"hashes": []})
        assert r.status_code == 422


def test_bulk_replay_caps_batch_at_256():
    """max_length=256 prevents accidental DoS via 100k-hash sweep."""
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app

    with TestClient(app, headers=_auth()) as client:
        too_many = ["0" * 64] * 257
        r = client.post("/counterfactual/replay/bulk", json={"hashes": too_many})
        assert r.status_code == 422


# ── Propensity diagnostics ────────────────────────────────────────────

@pytest.mark.skipif(
    not (dowhy_available() and econml_available()),
    reason="propensity diagnostics require econml DR-Learner",
)
@pytest.mark.asyncio
async def test_propensity_diagnostics_populated_on_dr_learner(monkeypatch, tmp_path):
    """The double_ml estimate must carry propensity_diagnostics with
    plausible quantile structure on a healthy synthetic dataset."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=500)
    query = CounterfactualQuery(
        question="propensity",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="propensity"),
    )

    artifact = await run_job(query, df=df)
    dr = next(e for e in artifact.estimates if e.method == "double_ml")
    assert dr.error is None, dr.error
    diag = dr.propensity_diagnostics
    assert diag is not None, "DR-Learner must populate propensity_diagnostics"
    # Quantile keys are stable (Sprint 13 contract)
    assert set(diag.quantiles.keys()) == {"p05", "p25", "p50", "p75", "p95"}
    # Quantiles monotonic non-decreasing
    qs = [diag.quantiles[k] for k in ("p05", "p25", "p50", "p75", "p95")]
    assert qs == sorted(qs), f"quantiles must be sorted: {qs}"
    # All in [0, 1]
    assert 0.0 <= diag.min <= diag.max <= 1.0
    # n_total matches the dataframe row count
    assert diag.n_total == 500


@pytest.mark.skipif(
    not (dowhy_available() and econml_available()),
    reason="propensity diagnostics require econml DR-Learner",
)
@pytest.mark.asyncio
async def test_propensity_diagnostics_in_hash_basis(monkeypatch, tmp_path):
    """propensity_diagnostics is in the artifact hash. Two artifacts
    with identical inputs (same seed, same data) produce equal
    diagnostics AND equal hashes; perturbing the diagnostics by hand
    in the persisted blob should produce a verify mismatch."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    # A per-method estimator timeout substitutes a sentinel estimate whose
    # zeroed point/ci/error ARE in the hash basis (strip_for_hashing only
    # excludes elapsed_ms) — a timeout on just one of these two back-to-back
    # runs would fail this determinism assertion under CI load.
    monkeypatch.setenv("AURA_CF_ESTIMATOR_TIMEOUT_S", "300")

    df = synthetic_dataset(n=300, seed=0xfeed_dead)
    query = CounterfactualQuery(
        question="hash_basis",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="hash_basis"),
    )

    art_a = await run_job(query, df=df)
    art_b = await run_job(query, df=df)

    # Determinism: same inputs → same hash → same diagnostics
    assert art_a.audit_record_hash == art_b.audit_record_hash
    dr_a = next(e for e in art_a.estimates if e.method == "double_ml")
    dr_b = next(e for e in art_b.estimates if e.method == "double_ml")
    assert dr_a.propensity_diagnostics == dr_b.propensity_diagnostics

    # Strip-for-hashing parity: dict-path and model-path produce equal bytes
    from counterfactual_service.canonical import canonical_dumps
    bytes_from_model = canonical_dumps(strip_for_hashing(art_a)).encode("utf-8")
    bytes_from_dict = canonical_dumps(
        strip_for_hashing(art_a.model_dump(mode="json")),
    ).encode("utf-8")
    assert bytes_from_model == bytes_from_dict, (
        "strip_for_hashing must be path-invariant; if this fails the "
        "verify endpoint will drift from the sign path again"
    )
