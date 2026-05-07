"""Pydantic model contracts."""
from __future__ import annotations

from aura_counterfactual import (
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
)


def test_artifact_parses_full_payload(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    assert art.record_id == "ca_test_1"
    assert art.audit_record_hash == "a" * 64
    assert art.signature_status == "signed"
    assert len(art.estimates) == 2
    assert len(art.refutations) == 2
    assert len(art.challenges) == 2


def test_artifact_ignores_unknown_extra_fields(sample_artifact):
    """Forward compatibility: unknown fields shipped by a newer engine
    must not crash the SDK."""
    payload = dict(sample_artifact, brand_new_field="from a future sprint")
    art = CounterfactualArtifact(**payload)
    assert art.record_id == "ca_test_1"


def test_artifact_average_point_excludes_errored_estimators(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    # PSM is in error → only LR contributes; average == LR.point
    assert art.average_point == 1.5


def test_artifact_average_point_none_when_all_errored(sample_artifact):
    payload = dict(sample_artifact)
    payload["estimates"] = [
        {"method": "linear_regression", "point": 0, "ci_lower": 0, "ci_upper": 0,
         "n_samples": 100, "error": "boom"},
    ]
    art = CounterfactualArtifact(**payload)
    assert art.average_point is None


def test_artifact_high_severity_challenges_filter(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    high = art.high_severity_challenges
    assert len(high) == 1
    assert high[0].text == "DAG omits a known confounder"


def test_artifact_succeeded_estimators(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    succ = art.succeeded_estimators
    assert len(succ) == 1
    assert succ[0].method == "linear_regression"


def test_artifact_ci_envelope_spans_all_valid_methods():
    """CI envelope is min/max over the valid estimators."""
    art = CounterfactualArtifact(
        record_id="ca_x",
        query=CounterfactualQuery(
            question="t",
            treatment={"column": "t", "actual": 1, "counterfactual": 0},
            outcome={"column": "y", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
            dag={"edges": [["t", "y"]]},
            dataset={"source_id": "ds"},
            audience="analyst",
        ),
        estimates=[
            CounterfactualEstimate(method="linear_regression", point=1.5,
                                    ci_lower=1.0, ci_upper=2.0, n_samples=100),
            CounterfactualEstimate(method="ipw", point=1.6,
                                    ci_lower=0.5, ci_upper=2.5, n_samples=100),
        ],
        confidence="high",
        schema_version="v1",
        dataset_fingerprint="x" * 64,
    )
    lo, hi = art.ci_envelope
    assert lo == 0.5
    assert hi == 2.5
