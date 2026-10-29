"""Audit round 11 (counterfactual area): BUG-549, BUG-550, BUG-551."""
from __future__ import annotations

import asyncio
import concurrent.futures

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

pytest.importorskip("dowhy", reason="counterfactual_service needs the causal stack")

from counterfactual_service import engine as engine_module  # noqa: E402
from counterfactual_service import main as m  # noqa: E402
from counterfactual_service import persistence  # noqa: E402
from counterfactual_service.schemas import (  # noqa: E402
    CounterfactualEstimate,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from counterfactual_service.verdict import rule_for, significance_verdict  # noqa: E402
from shared.auth import create_access_token  # noqa: E402
from tests._mock_llm import UnifiedMockLLM, install_mock  # noqa: E402
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset  # noqa: E402


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'r11', 'org_id': 'org-r11'})}"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", bytes(range(32)).hex())

    async def _no_refuters(*a, **k):
        return []
    monkeypatch.setattr(engine_module, "run_refuters", _no_refuters)
    return tmp_path


def _seal(question: str) -> str:
    query = CounterfactualQuery(
        question=question,
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="r11"),
    )
    art = asyncio.run(engine_module.run_job(query, df=synthetic_dataset(n=400),
                                            methods=["linear_regression"]))
    assert art.signature_status == "signed"
    return art.audit_record_hash


# ── BUG-549: verify must bind the content to the record hash in the URL ──


def test_a_signed_artifact_copied_over_another_hash_does_not_verify(store):
    a, b = _seal("bug-549 record A"), _seal("bug-549 record B")
    assert a != b
    assert asyncio.run(m.verify_artifact(b))["verified"] is True

    persistence.write_artifact(b, persistence.read_artifact(a))
    persistence.write_signature(b, persistence.read_signature(a))

    swapped = asyncio.run(m.verify_artifact(b))
    assert swapped["verified"] is False
    assert swapped["reason"] == "content does not hash to this record hash"
    assert m._verify_one_artifact(b)["status"] != "ok"
    assert asyncio.run(m.verify_artifact(a))["verified"] is True
    assert m._verify_one_artifact(a)["status"] == "ok"


def test_an_edited_unsigned_verdict_is_not_served(store):
    h = _seal("bug-549 forged verdict")
    stored = persistence.read_artifact(h)
    forged = {"status": "not_material", "label": "FORGED: no disparate impact"}
    persistence.write_artifact(h, {**stored, "rendered": {"verdict": forged, "headline": "kept"}})

    served = asyncio.run(m.get_artifact(h, user=None))
    expected = significance_verdict(stored["estimates"], rule_for(stored))
    assert served["rendered"]["verdict"] == expected
    assert served["rendered"]["verdict"]["label"] != forged["label"]
    assert served["rendered"]["headline"] == "kept"


# ── BUG-551: a non-lowercase hash must not 500 on the public verify routes ──


def test_uppercase_hash_verifies_as_its_lowercase_record(store):
    h = _seal("bug-551 uppercase")
    v = asyncio.run(m.verify_artifact(h.upper()))
    assert v["verified"] is True and v["record_hash"] == h
    assert asyncio.run(m.get_artifact(h.upper(), user=None))["audit_record_hash"] == h


@pytest.mark.parametrize("path", [
    "/counterfactual/artifacts/{h}/verify",
    "/counterfactual/artifacts/{h}",
    "/counterfactual/artifacts/{h}/report.pdf",
    "/audit/financial/verify/{h}",
])
def test_malformed_hash_is_a_client_error_not_a_500(store, path):
    client = TestClient(m.app, raise_server_exceptions=False)
    assert client.get(path.format(h="Z" * 64), headers=_auth()).status_code == 400
    assert client.get(path.format(h="a" * 64), headers=_auth()).status_code in (200, 404)


def test_uppercase_financial_hash_is_not_a_500(store):
    client = TestClient(m.app, raise_server_exceptions=False)
    r = client.get("/audit/financial/verify/" + "A" * 64)
    assert r.status_code == 200
    assert r.json()["verified"] is False


# ── BUG-550: live audits are capped and kept off the default executor ──


def _fill_live_jobs(monkeypatch, n: int):
    jobs = {f"ca_live{i}": {"state": "running", "artifact": None, "error": None, "tenant": "t"}
            for i in range(n)}
    monkeypatch.setattr(m, "_jobs", jobs)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(m, "_run_demo_async", _noop)
    monkeypatch.setattr(m, "_run_async", _noop)


def test_live_audits_beyond_the_cap_are_refused(monkeypatch):
    _fill_live_jobs(monkeypatch, m.MAX_LIVE_AUDIT_JOBS)
    with pytest.raises(HTTPException) as demo:
        asyncio.run(m.run_demo("fair_lending", fresh=True, user={"sub": "u", "org_id": "o"}))
    assert demo.value.status_code == 429
    query = m.get_scenario("fair_lending").query()
    with pytest.raises(HTTPException) as job:
        asyncio.run(m.submit_job(query, user={"sub": "u", "org_id": "o"}))
    assert job.value.status_code == 429


def test_a_live_audit_under_the_cap_is_accepted(monkeypatch):
    _fill_live_jobs(monkeypatch, m.MAX_LIVE_AUDIT_JOBS - 1)
    out = asyncio.run(m.run_demo("fair_lending", fresh=True, user={"sub": "u", "org_id": "o"}))
    assert out["cached"] is False


class _RefusingExecutor(concurrent.futures.ThreadPoolExecutor):
    def submit(self, *a, **k):
        raise AssertionError("engine work reached the event loop's default executor")


def test_engine_work_never_uses_the_default_executor(monkeypatch):
    def _stub(method_key, df, *a, **k):
        return CounterfactualEstimate(method=method_key, point=0.0, ci_lower=0.0,
                                      ci_upper=0.0, n_samples=len(df), error="stub")
    monkeypatch.setattr(engine_module, "_run_one_estimator", _stub)
    df = synthetic_dataset(n=300)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    dag = synthetic_dag_full()

    async def _go():
        asyncio.get_running_loop().set_default_executor(_RefusingExecutor(max_workers=1))
        est = await engine_module.run_estimators(df, treatment, outcome, dag,
                                                 methods=["linear_regression"])
        ref = await engine_module.run_refuters(df, treatment, outcome, dag,
                                               refuters=["placebo"])
        return est, ref

    est, ref = asyncio.run(_go())
    assert [e.error for e in est] == ["stub"]
    assert len(ref) == 1 and "default executor" not in (ref[0].error or "")
