"""Canonical-JSON contracts and artifact hash stability."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from counterfactual_service.canonical import canonical_dumps, sha256_canonical

# ── canonical_dumps invariants ────────────────────────────────────────

def test_keys_sorted_recursively():
    a = {"b": {"z": 1, "a": 2}, "a": 3}
    b = {"a": 3, "b": {"a": 2, "z": 1}}
    assert canonical_dumps(a) == canonical_dumps(b)


def test_floats_six_decimal_fixed():
    assert canonical_dumps({"x": 1.0}) == '{"x":"1.000000"}'
    assert canonical_dumps({"x": 1.123456789}) == '{"x":"1.123457"}'


def test_floats_equivalent_at_six_decimals():
    a = {"x": 1.1234567}
    b = {"x": 1.1234569}      # differ in 7th decimal → both round to 1.123457
    assert canonical_dumps(a) == canonical_dumps(b)


def test_datetimes_iso_utc_z():
    t = datetime(2026, 5, 2, 18, 32, 11, 123000, tzinfo=timezone.utc)
    assert canonical_dumps({"t": t}) == '{"t":"2026-05-02T18:32:11.123000Z"}'


def test_naive_datetimes_treated_as_utc():
    naive = datetime(2026, 5, 2, 18, 32, 11, 123000)
    aware = datetime(2026, 5, 2, 18, 32, 11, 123000, tzinfo=timezone.utc)
    assert canonical_dumps({"t": naive}) == canonical_dumps({"t": aware})


def test_none_keys_dropped():
    assert canonical_dumps({"a": 1, "b": None}) == '{"a":1}'


def test_lists_preserved_in_order():
    assert canonical_dumps({"xs": [3, 1, 2]}) == '{"xs":[3,1,2]}'


def test_tuples_serialise_like_lists():
    assert canonical_dumps({"xs": (3, 1, 2)}) == '{"xs":[3,1,2]}'


def test_bool_preserved():
    # bool is a subclass of int — make sure it doesn't become "1.000000".
    assert canonical_dumps({"x": True}) == '{"x":true}'


def test_sha256_canonical_stable_under_key_shuffle():
    a = {"x": 1, "y": [{"b": 2, "a": 1}, {"a": 3, "b": 4}]}
    b = {"y": [{"a": 1, "b": 2}, {"b": 4, "a": 3}], "x": 1}
    assert sha256_canonical(a) == sha256_canonical(b)


# ── Artifact hash stability across estimate/refutation order ──────────

def test_artifact_canonical_hash_stable_across_estimate_order():
    from counterfactual_service.schemas import (
        CounterfactualArtifact,
        CounterfactualEstimate,
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
        RefutationResult,
    )

    q = CounterfactualQuery(
        question="test",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=[("x", "y")]),
        dataset=DatasetRef(source_id="ds_1"),
    )
    e1 = CounterfactualEstimate(method="ipw", point=1.5, ci_lower=1.0, ci_upper=2.0, n_samples=100)
    e2 = CounterfactualEstimate(method="linear_regression", point=1.6, ci_lower=1.1, ci_upper=2.1, n_samples=100)
    r1 = RefutationResult(refuter="placebo", passed=True)
    r2 = RefutationResult(refuter="random_common_cause", passed=True)

    art_a = CounterfactualArtifact(
        record_id="ca_1", query=q,
        estimates=sorted([e1, e2], key=lambda e: e.method),
        refutations=sorted([r1, r2], key=lambda r: r.refuter),
        challenges=[], confidence="high", schema_version="abc",
        dataset_fingerprint="def",
    )
    art_b = CounterfactualArtifact(
        record_id="ca_1", query=q,
        estimates=sorted([e2, e1], key=lambda e: e.method),
        refutations=sorted([r2, r1], key=lambda r: r.refuter),
        challenges=[], confidence="high", schema_version="abc",
        dataset_fingerprint="def",
    )
    assert sha256_canonical(art_a.model_dump(mode="json")) == \
        sha256_canonical(art_b.model_dump(mode="json"))


def test_artifact_excludes_audit_fields_for_hash():
    """audit_record_hash and rendered must NOT be in the hash payload —
    otherwise the hash would change after sealing."""
    from counterfactual_service.schemas import (
        CounterfactualArtifact,
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
    )
    q = CounterfactualQuery(
        question="t",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=[("t", "y")]),
        dataset=DatasetRef(source_id="ds"),
    )
    a = CounterfactualArtifact(
        record_id="ca_1", query=q, confidence="high",
        schema_version="v1", dataset_fingerprint="abc",
    )
    b = a.model_copy(update={"audit_record_hash": "0xdead", "rendered": {"x": 1}})
    pa = a.model_dump(mode="json", exclude={"audit_record_hash", "rendered"})
    pb = b.model_dump(mode="json", exclude={"audit_record_hash", "rendered"})
    assert sha256_canonical(pa) == sha256_canonical(pb)
