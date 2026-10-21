"""Audit round 7, counterfactual signing area: BUG-485, BUG-487."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

# ── BUG-487: formatted amount strings must not be zeroed ──────────────


def _quiet_auditor(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    return fa, fa.FinancialAuditorAgent(tenant_id="t-bug487")


@pytest.mark.parametrize("raw, expected", [
    ("1,250,000.00", Decimal("1250000.00")),
    ("$5,000", Decimal("5000")),
    (" $1,234.50 ", Decimal("1234.50")),
    (1250000.0, Decimal("1250000.0")),
    ("not a number", Decimal(0)),
    (None, Decimal(0)),
])
def test_money_parses_formatted_amounts(raw, expected):
    from agents.specialists.financial_auditor import _money
    assert _money(raw) == expected


def test_formatted_material_entry_is_flagged_like_the_numeric_one(monkeypatch):
    _, agent = _quiet_auditor(monkeypatch)
    filler = [{"internal_id": f"F{i}", "account_code": "5000", "amount": 1234.5} for i in range(18)]

    def run(amount):
        ledger = [{"internal_id": "L1", "account_code": "4000", "amount": amount}] + filler
        return asyncio.run(agent.run_full_audit(ledger, [], [], []))

    numeric = run(1250000.0)
    formatted = run("1,250,000.00")
    assert formatted["materiality_threshold"] == pytest.approx(numeric["materiality_threshold"])
    hits = [f for f in formatted["findings"]
            if f.evidence_payload.get("test") == "absolute_materiality"]
    assert [f.evidence_payload["entry_id"] for f in hits] == ["L1"]


def test_formatted_duplicate_payment_is_detected(monkeypatch):
    _, agent = _quiet_auditor(monkeypatch)
    jes = [{"internal_id": "J1", "amount": "$5,000.00", "account_code": "6000", "vendor_id": "V1"},
           {"internal_id": "J2", "amount": "$5,000.00", "account_code": "6000", "vendor_id": "V1"}]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    assert any("duplicate" in f.description.lower() for f in findings)


# ── BUG-485: an unsigned re-seal must not clobber a signed artifact ───


@pytest.mark.asyncio
async def test_unsigned_reseal_keeps_the_signed_artifact_verifiable(monkeypatch, tmp_path):
    pytest.importorskip("dowhy")
    from counterfactual_service import engine as engine_module
    from counterfactual_service.main import verify_artifact
    from counterfactual_service.schemas import (
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
    )
    from tests._mock_llm import UnifiedMockLLM, install_mock
    from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", bytes(range(32)).hex())

    # Refuters are stochastic; stubbing them keeps the content hash identical
    # across the two runs, which is the replay case the bug is about.
    async def _no_refuters(*a, **k):
        return []
    monkeypatch.setattr(engine_module, "run_refuters", _no_refuters)

    df = synthetic_dataset(n=400)
    query = CounterfactualQuery(
        question="bug-485 replay after revocation",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="bug-485"),
    )

    first = await engine_module.run_job(query, df=df, methods=["linear_regression"])
    assert first.signature_status == "signed"
    h = first.audit_record_hash
    assert (await verify_artifact(h))["verified"] is True

    engine_module.cryptography.soft_revoke_key()
    second = await engine_module.run_job(query, df=df, methods=["linear_regression"])
    assert second.audit_record_hash == h
    assert second.signature_status == "unsigned"

    after = await verify_artifact(h)
    assert after["verified"] is True
    assert after["signature_status"] == "signed"
