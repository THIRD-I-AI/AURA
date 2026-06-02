"""S33: ONE significance-aware verdict, shared by the PDF, the chat headline, and
(via the artifact) the web certificate — so a signed compliance artifact never
overclaims or self-contradicts. The rule mirrors the frontend significance test
shipped in S31f (frontend/src/audit/Certificate.tsx::verdict)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service import pdf_renderer
from counterfactual_service.renderers import render
from counterfactual_service.schemas import (
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from counterfactual_service.verdict import significance_verdict


def _est(point, lo=None, hi=None, error=None):
    return {"point": point, "ci_lower": lo, "ci_upper": hi, "error": error}


def test_empty_when_all_errored():
    v = significance_verdict([_est(0.5, 0.4, 0.6, error="boom")])
    assert v["status"] == "empty"


def test_empty_when_no_estimates():
    assert significance_verdict([])["status"] == "empty"


def test_not_material_below_threshold():
    v = significance_verdict([_est(0.01, -0.2, 0.2)])
    assert v["status"] == "not_material"
    assert "no material" in v["label"].lower()


def test_material_but_ci_crosses_zero_is_not_significant():
    # The real adjusted-COMPAS case: material point (~-0.024) but the CI spans 0.
    v = significance_verdict([_est(-0.024, -0.08, 0.03)])
    assert v["status"] == "not_significant"
    assert "not statistically significant" in v["label"].lower()


def test_detected_when_material_and_all_ci_exclude_zero():
    v = significance_verdict([_est(0.16, 0.06, 0.25), _est(0.14, 0.05, 0.22)])
    assert v["status"] == "detected"
    assert "detected" in v["label"].lower()


def test_any_straddling_ci_downgrades_to_not_significant():
    v = significance_verdict([_est(0.16, 0.06, 0.25), _est(0.10, -0.01, 0.21)])
    assert v["status"] == "not_significant"


def test_accepts_model_attributes_and_string_numbers():
    # _headline passes Pydantic models; the replay path serializes numbers as strings.
    est = SimpleNamespace(point="0.16", ci_lower="0.06", ci_upper="0.25", error=None)
    assert significance_verdict([est])["status"] == "detected"


def test_material_with_no_cis_falls_through_to_detected():
    # Matches S31f: with no finite CIs to test, a material point is "detected".
    assert significance_verdict([{"point": 0.5, "error": None}])["status"] == "detected"


# ── Integration: the two backend surfaces must route through the one rule ──


def _query():
    return CounterfactualQuery(
        question="effect of t on y",
        treatment=InterventionSpec(column="t", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=DAGSpec(edges=[("x", "t"), ("x", "y"), ("t", "y")]),
        dataset=DatasetRef(source_id="inline"),
    )


def _artifact(estimates):
    return CounterfactualArtifact(
        record_id="r1", query=_query(), estimates=estimates,
        confidence="low", schema_version="1.0", dataset_fingerprint="fp",
    )


def _estimate(point, lo, hi):
    return CounterfactualEstimate(method="tmle", point=point, ci_lower=lo,
                                  ci_upper=hi, n_samples=100)


def test_rendered_headline_and_verdict_are_significance_aware():
    # COMPAS-style: material magnitude, CI straddles zero → must NOT claim detected.
    out = render(_artifact([_estimate(-0.024, -0.08, 0.03)]), "auditor")
    assert out["verdict"]["status"] == "not_significant"
    assert "not statistically significant" in out["headline"].lower()


def test_rendered_verdict_detected_when_significant():
    out = render(_artifact([_estimate(0.16, 0.06, 0.25)]), "operator")
    assert out["verdict"]["status"] == "detected"
    assert "detected" in out["headline"].lower()


def test_pdf_verdict_text_not_significant():
    art = {"estimates": [{"point": -0.024, "ci_lower": -0.08, "ci_upper": 0.03, "error": None}]}
    txt = pdf_renderer._verdict_text(art)
    assert txt is not None and "not statistically significant" in txt.lower()


def test_pdf_verdict_text_detected():
    art = {"estimates": [{"point": 0.16, "ci_lower": 0.06, "ci_upper": 0.25, "error": None}]}
    assert "disparate impact detected" in pdf_renderer._verdict_text(art).lower()
