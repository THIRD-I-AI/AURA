"""A signed artifact attests to what the engine did, not to what the query asked for.

Closes the class behind BUG-402, 405, 419, 420 and 421: a report line or signed
field that the computation never honoured. Every field of the query is classified
here as APPLIED (it shapes the result), REFUSED (submit rejects it) or RECORDED_ONLY
(stored, declared as unused in the artifact's applied spec). A new query field fails
this test until someone decides which it is.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

import counterfactual_service.main as cf_main
from counterfactual_service.engine import run_job, strip_for_hashing
from counterfactual_service.schemas import (
    CounterfactualArtifact,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from counterfactual_service.verdict import CURRENT_RULE, LEGACY_RULE, rule_for, significance_verdict
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

APPLIED = {
    "treatment.column", "treatment.actual", "treatment.counterfactual",
    "outcome.column", "dag.edges", "dataset.source_id",
}
REFUSED = {"dataset.where", "dataset.limit"}
RECORDED_ONLY = {"outcome.agg", "outcome.window"}
# Shape how the result is presented, never the estimate.
PRESENTATION = {"question", "audience"}


def _leaf_fields(model: type[BaseModel], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            out |= _leaf_fields(annotation, f"{prefix}{name}.")
        else:
            out.add(f"{prefix}{name}")
    return out


def test_every_query_field_is_classified():
    fields = _leaf_fields(CounterfactualQuery)
    classified = APPLIED | REFUSED | RECORDED_ONLY | PRESENTATION
    assert fields - classified == set(), "new query field: is it applied, refused, or recorded only?"
    assert classified - fields == set(), "stale classification"


def _query(**dataset) -> CounterfactualQuery:
    return CounterfactualQuery(
        question="contract",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-07-01", "2025-09-30")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="contract", **dataset),
    )


@pytest.fixture()
def artifact_env(monkeypatch, tmp_path):
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_the_artifact_records_what_was_applied(artifact_env):
    df = synthetic_dataset(n=500)
    artifact = await run_job(_query(), df=df)

    applied = artifact.applied
    assert applied is not None
    assert applied.rows_used == len(df)
    assert applied.adjustment_set == ["seasonality"]
    assert applied.instruments == []
    assert applied.verdict_rule == CURRENT_RULE
    assert set(applied.recorded_only) == RECORDED_ONLY
    # ...and it is inside the signed payload.
    assert strip_for_hashing(artifact)["applied"]["rows_used"] == len(df)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", sorted(REFUSED))
async def test_a_refused_field_is_rejected_at_submit(monkeypatch, field):
    from fastapi import HTTPException

    async def _no_run(job_id, query):
        raise AssertionError("a refused query must not start a job")

    monkeypatch.setattr(cf_main, "_run_async", _no_run)
    value = {"dataset.where": {"where": "region = 'EU'"}, "dataset.limit": {"limit": 10}}[field]
    with pytest.raises(HTTPException) as refused:
        await cf_main.submit_job(_query(**value), user={"sub": "u1"})
    assert refused.value.status_code == 422


def _legacy_artifact_dict() -> dict:
    """An artifact as stored before the applied spec existed: no ``applied`` key."""
    art = CounterfactualArtifact(
        record_id="ca_legacy", query=_query(), confidence="medium",
        schema_version="v1", dataset_fingerprint="f" * 16,
    )
    stored = art.model_dump(mode="json")
    stored.pop("applied")
    return stored


def test_an_artifact_sealed_before_the_applied_spec_keeps_its_signed_bytes():
    stored = _legacy_artifact_dict()
    payload = strip_for_hashing(stored)
    assert "applied" not in payload
    # Exactly the keys that were signed back then: nothing added by the new model.
    expected = {k for k in stored if k not in {
        "audit_record_hash", "rendered", "signature_b64", "signature_status",
        "signing_key_source", "record_id", "regenerated_critic",
    }}
    assert expected <= set(payload) | {"created_at"}
    assert json.dumps(payload, sort_keys=True) == json.dumps(strip_for_hashing(json.loads(json.dumps(stored))), sort_keys=True)


def test_an_artifact_is_re_rendered_with_the_rule_it_was_sealed_under():
    assert rule_for(_legacy_artifact_dict()) == LEGACY_RULE
    assert rule_for({"applied": {"verdict_rule": CURRENT_RULE}}) == CURRENT_RULE

    # Opposite signs: the legacy rule averages them to "not material"; the current
    # rule does not let them cancel.
    estimates = [
        {"point": 0.10, "ci_lower": 0.06, "ci_upper": 0.14, "error": None},
        {"point": -0.10, "ci_lower": -0.14, "ci_upper": -0.06, "error": None},
    ]
    assert significance_verdict(estimates, LEGACY_RULE)["status"] == "not_material"
    assert significance_verdict(estimates, CURRENT_RULE)["status"] == "inconsistent"
