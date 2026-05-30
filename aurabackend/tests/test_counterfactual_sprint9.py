"""
Sprint 9 — Auditor view tests.

Covers:
* Persistence round-trip (canonical-JSON byte stability)
* ED25519 signing + verification (env-keyed and ephemeral paths)
* Critic-cache hit/miss with the ``regenerated_critic`` transparency flag
* Replay endpoint returns byte-identical persisted artifact (eval-gate Layer 10)
* PDF renderer presence + smoke generation
* Public-key endpoint serves a usable PEM
"""
from __future__ import annotations

import json
import os

import pytest

from counterfactual_service import (
    critic_cache,
    pdf_renderer,
    persistence,
    signing,
)
from counterfactual_service.canonical import canonical_dumps, sha256_canonical
from counterfactual_service.engine import dowhy_available, run_job
from counterfactual_service.main import register_dataset
from counterfactual_service.schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import (
    synthetic_dag_full,
    synthetic_dataset,
)

# Tests that touch the engine require dowhy.
ENGINE_TESTS = pytest.mark.skipif(
    not dowhy_available(),
    reason="dowhy required for engine round-trip tests",
)


# ── Persistence ───────────────────────────────────────────────────────

def test_persistence_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True, "f": 1.234567}}
    record_hash = sha256_canonical(payload)

    persistence.write_artifact(record_hash, payload)
    back = persistence.read_artifact(record_hash)
    assert back is not None
    assert back == json.loads(canonical_dumps(payload))


def test_persistence_atomic_overwrite(tmp_path, monkeypatch):
    """Writing twice with the same record_hash is idempotent."""
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    payload = {"x": "y"}
    record_hash = sha256_canonical(payload)
    persistence.write_artifact(record_hash, payload)
    bytes_a = persistence.read_artifact_bytes(record_hash)
    persistence.write_artifact(record_hash, payload)
    bytes_b = persistence.read_artifact_bytes(record_hash)
    assert bytes_a == bytes_b


def test_persistence_unknown_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    assert persistence.read_artifact("0" * 64) is None
    assert persistence.read_artifact_bytes("0" * 64) is None


def test_persistence_rejects_non_hex(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        persistence.write_artifact("../etc/passwd", {})


def test_persistence_falls_back_to_audit_dir_parent(tmp_path, monkeypatch):
    """If only AURA_AUDIT_DIR is set, artifact dir sits next to it."""
    monkeypatch.delenv("AURA_ARTIFACT_DIR", raising=False)
    audit = tmp_path / "audit"
    audit.mkdir()
    monkeypatch.setenv("AURA_AUDIT_DIR", str(audit))
    d = persistence.artifact_dir()
    assert d.parent == tmp_path
    assert d.name == "artifacts"


# ── Signing ───────────────────────────────────────────────────────────

@pytest.fixture
def fresh_signing(monkeypatch):
    """Reset the cached signing keypair so each test resolves fresh."""
    import counterfactual_service.signing as s
    monkeypatch.setattr(s, "_KEY_PAIR", None)
    monkeypatch.setattr(s, "_KEY_SOURCE", "uninitialized")
    yield


def test_signing_available_when_cryptography_installed():
    assert signing.signing_available() is True


def test_signing_with_env_hex_key(monkeypatch, fresh_signing):
    # Deterministic 32-byte seed
    seed = bytes(range(32)).hex()
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", seed)
    sig_b64 = signing.sign_bytes(b"hello world")
    assert sig_b64
    assert signing.verify_bytes(b"hello world", sig_b64) is True
    assert signing.verify_bytes(b"hello WORLD", sig_b64) is False
    assert signing.signing_key_source() == "env_hex"


def test_signing_persists_to_file_when_no_env(monkeypatch, fresh_signing, tmp_path):
    # S31b: with no env key the resolver auto-generates AND persists a key
    # (so certificate signatures survive restarts), rather than going
    # ephemeral. Ephemeral is now only the last-resort fallback when the key
    # dir is unwritable — covered by test_signing_persistent_key.py.
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))
    sig = signing.sign_bytes(b"x")
    assert sig
    assert signing.signing_key_source() == "persisted_file"
    assert (tmp_path / "signing_ed25519.pem").exists()


def test_signing_invalid_hex_falls_through_to_persisted(monkeypatch, fresh_signing, tmp_path):
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", "not-hex-at-all")
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))
    sig = signing.sign_bytes(b"x")
    assert sig
    # Invalid hex falls through to the persisted-file source (warning logged).
    assert signing.signing_key_source() == "persisted_file"


def test_public_key_pem_usable_for_external_verify(monkeypatch, fresh_signing):
    seed = bytes(range(32)).hex()
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", seed)
    pem = signing.public_key_pem()
    assert pem and "BEGIN PUBLIC KEY" in pem and "END PUBLIC KEY" in pem
    # Round-trip: external loader can verify a signature using just the PEM.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(pem.encode("ascii"))
    sig_b64 = signing.sign_bytes(b"hello")
    import base64
    sig = base64.b64decode(sig_b64)
    pub.verify(sig, b"hello")  # raises InvalidSignature on mismatch
    with pytest.raises(InvalidSignature):
        pub.verify(sig, b"hellb")


# ── Critic cache ──────────────────────────────────────────────────────

def test_critic_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path))
    key = critic_cache.cache_key(
        request_hash="abc", model_id="groq:llama", model_version="1.0",
    )
    assert critic_cache.get(key) is None
    # Canonical-JSON drops None-valued keys; round-trip preserves only
    # the fields the engine actually emits as non-null.
    challenges = [{"text": "t", "severity": "low", "suggested_check": "do x"}]
    critic_cache.put(key, challenges)
    got = critic_cache.get(key)
    assert got is not None
    assert {(c["text"], c["severity"], c.get("suggested_check")) for c in got} == \
           {(c["text"], c["severity"], c["suggested_check"]) for c in challenges}


def test_critic_cache_key_stable_across_dict_construction_order():
    a = critic_cache.cache_key(request_hash="a", model_id="m", model_version="v")
    b = critic_cache.cache_key(model_version="v", model_id="m", request_hash="a")
    assert a == b


def test_critic_cache_key_changes_on_model_version_bump():
    a = critic_cache.cache_key(request_hash="a", model_id="m", model_version="v1")
    b = critic_cache.cache_key(request_hash="a", model_id="m", model_version="v2")
    assert a != b


# ── End-to-end: replay contract (eval-gate Layer 10) ──────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_layer10_replay_returns_byte_identical_artifact(monkeypatch, tmp_path):
    """Submit job → record audit_record_hash → fetch via replay endpoint
    → assert the returned artifact's audit_record_hash matches *and* the
    re-canonicalised content matches.

    This is the Sprint 9 eval-gate layer 10 contract.
    """
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    seed = bytes(range(32)).hex()
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", seed)

    df = synthetic_dataset(n=400)
    query = CounterfactualQuery(
        question="layer 10",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="layer10"),
    )

    # First run — seals to disk
    art_a = await run_job(query, df=df)
    assert art_a.audit_record_hash
    assert art_a.signature_status == "signed"

    # Replay — read back the persisted artifact
    persisted = persistence.read_artifact(art_a.audit_record_hash)
    assert persisted is not None
    assert persisted["audit_record_hash"] == art_a.audit_record_hash

    # Re-canonicalise the persisted dict; the canonical bytes must match
    # what was written. (Persistence wrote canonical bytes already, so a
    # round-trip parse + canonical_dumps re-emits identically.)
    persisted_bytes = persistence.read_artifact_bytes(art_a.audit_record_hash)
    assert canonical_dumps(persisted) == persisted_bytes.decode("utf-8")


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_critic_cache_hit_sets_regenerated_critic_to_false(monkeypatch, tmp_path):
    """The Sprint 9 critic-cache contract: same query, second call hits
    the cache, ``regenerated_critic`` flips to False, and the cached
    challenge text is byte-identical.

    *Full* artifact-hash byte-stability across re-runs additionally
    requires pinning DoWhy's per-method RNG seeds (PSM/IPW are
    stochastic). That's Sprint 11+ scope (spec §4.5 + Risk #8); this
    test verifies the cache half of the determinism contract that S9
    actually delivers."""
    install_mock(
        monkeypatch,
        UnifiedMockLLM(default_response='{"challenges": [{"text": "n_samples small", "severity": "low"}]}'),
    )
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))

    df = synthetic_dataset(n=300)
    query = CounterfactualQuery(
        question="cache",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="cache"),
    )

    a = await run_job(query, df=df)
    assert a.regenerated_critic is True   # first call: cache miss, regenerated

    b = await run_job(query, df=df)
    assert b.regenerated_critic is False  # second call: cache hit

    # Critic content is byte-identical via the cache.
    a_challenges = [(c.text, c.severity) for c in a.challenges]
    b_challenges = [(c.text, c.severity) for c in b.challenges]
    assert a_challenges == b_challenges

    # Dataset fingerprint is fully deterministic.
    assert a.dataset_fingerprint == b.dataset_fingerprint

    # The audit_record_hash MAY differ across two engine re-runs because
    # DoWhy estimators have unpinned random sources. Replay
    # (eval-gate Layer 10) reads the *persisted* bytes — that path is
    # already byte-stable, see test_layer10_replay_returns_byte_identical_artifact.


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_replay_endpoint_returns_artifact(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from counterfactual_service.main import app

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    seed = bytes(range(32)).hex()
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", seed)
    register_dataset("replay_svc", synthetic_dataset(n=300))

    payload = {
        "question": "replay",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome":   {"column": "outcome", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        "dag":       {"edges": synthetic_dag_full()["edges"]},
        "dataset":   {"source_id": "replay_svc"},
        "audience":  "auditor",
    }

    with TestClient(app) as client:
        r = client.post("/counterfactual/jobs", json=payload)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        # Poll
        import time
        for _ in range(120):
            sr = client.get(f"/counterfactual/jobs/{job_id}").json()
            if sr["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)
        assert sr["state"] == "succeeded", sr
        record_hash = sr["artifact"]["audit_record_hash"]

        replay = client.get(f"/counterfactual/artifacts/{record_hash}")
        assert replay.status_code == 200
        assert replay.json()["audit_record_hash"] == record_hash

        # 404 on unknown record_hash
        assert client.get("/counterfactual/artifacts/" + "0" * 64).status_code == 404

        # Verify endpoint
        v = client.get(f"/counterfactual/artifacts/{record_hash}/verify").json()
        assert v["verified"] is True, v
        assert v["signature_status"] == "signed"

        # Public key endpoint
        pk = client.get("/counterfactual/public-key").json()
        assert "BEGIN PUBLIC KEY" in pk["public_key_pem"]


# ── PDF renderer ──────────────────────────────────────────────────────

@pytest.mark.skipif(not pdf_renderer.pdf_available(),
                    reason="reportlab not installed")
def test_pdf_renderer_produces_pdf_bytes():
    artifact = {
        "record_id": "ca_test",
        "query": {
            "question": "Test counterfactual?",
            "treatment": {"column": "t", "actual": 1, "counterfactual": 0},
            "outcome":   {"column": "y", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        },
        "estimates": [
            {"method": "linear_regression", "point": 1.5, "ci_lower": 1.0, "ci_upper": 2.0,
             "n_samples": 100, "elapsed_ms": 12.3, "error": None},
            {"method": "psm", "point": 0, "ci_lower": 0, "ci_upper": 0,
             "n_samples": 100, "elapsed_ms": 11.0, "error": "binary treatment required"},
        ],
        "refutations": [
            {"refuter": "placebo", "estimate_after": 0.02, "p_value": 0.04, "passed": True,
             "elapsed_ms": 5.5, "error": None},
        ],
        "challenges": [
            {"text": "n_samples is small", "severity": "low", "suggested_check": "collect more"},
            {"text": "DAG omits seasonality", "severity": "high", "suggested_check": None},
        ],
        "confidence": "medium",
        "schema_version": "v1",
        "dataset_fingerprint": "f" * 64,
        "audit_record_hash": "a" * 64,
        "signature_status": "signed",
        "signing_key_source": "env_hex",
        "regenerated_critic": False,
    }
    pdf = pdf_renderer.render_pdf(artifact)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000  # smoke: a one-pager is at least a couple KB


def test_pdf_endpoint_501_when_renderer_unavailable(monkeypatch, tmp_path):
    """If reportlab isn't installed in a deployment, the PDF endpoint
    returns 501 (Not Implemented) rather than 503. 501 signals the
    feature is *deterministically* unavailable in this deployment;
    SDK clients use that to skip retries (whereas 503 means transient
    'service temporarily unavailable, please back off and retry')."""
    monkeypatch.setattr(pdf_renderer, "_REPORTLAB_AVAILABLE", False)
    monkeypatch.setattr(pdf_renderer, "pdf_available", lambda: False)
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))

    # Persist a minimal artifact so the endpoint reaches the renderer
    payload = {"record_id": "ca_x", "audit_record_hash": "a" * 64}
    persistence.write_artifact("a" * 64, payload)

    from fastapi.testclient import TestClient

    from counterfactual_service.main import app
    with TestClient(app) as client:
        r = client.get("/counterfactual/artifacts/" + "a" * 64 + "/report.pdf")
        assert r.status_code == 501
        assert "reportlab" in r.text
