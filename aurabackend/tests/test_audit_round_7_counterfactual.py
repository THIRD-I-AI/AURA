"""Audit round 7 (counterfactual area): BUG-480."""
from __future__ import annotations

from counterfactual_service import main as cf_main
from counterfactual_service.pdf_renderer import _verdict_text
from counterfactual_service.verdict import CURRENT_RULE, LEGACY_RULE


def _stored(rule, estimates):
    # The shape run_job persists: no ``rendered`` block yet.
    return {
        "record_id": "r1",
        "audit_record_hash": "h1",
        "applied": {"estimand": "ate", "rows_used": 100, "verdict_rule": rule},
        "estimates": estimates,
        "rendered": {},
    }


# A small but significant effect: under the v2 rule (the one the signed PDF
# applies to a v2 artifact) this is "detected"; the v1 0.02 floor called it
# "not material", which is what the certificate page showed.
_SMALL_SIGNIFICANT = [
    {"method": "double_ml", "point": 0.015, "ci_lower": 0.012, "ci_upper": 0.018,
     "sensitivity": {"standardised_effect_d": 0.30}},
    {"method": "tmle", "point": 0.016, "ci_lower": 0.010, "ci_upper": 0.022,
     "sensitivity": {"standardised_effect_d": 0.32}},
]


def test_a_replayed_artifact_carries_the_verdict_the_pdf_prints():
    art = cf_main._artifact_for_caller(_stored(CURRENT_RULE, _SMALL_SIGNIFICANT), "h1", None)
    verdict = art["rendered"]["verdict"]
    assert verdict["status"] == "detected"
    assert verdict["label"] in _verdict_text(art)


def test_opposite_significant_estimates_replay_as_inconsistent():
    est = [
        {"method": "double_ml", "point": 0.5, "ci_lower": 0.3, "ci_upper": 0.7},
        {"method": "tmle", "point": -0.5, "ci_lower": -0.7, "ci_upper": -0.3},
    ]
    art = cf_main._artifact_for_caller(_stored(CURRENT_RULE, est), "h1", None)
    assert art["rendered"]["verdict"]["status"] == "inconsistent"


def test_a_legacy_artifact_keeps_its_sealed_rule():
    art = cf_main._artifact_for_caller(_stored(LEGACY_RULE, _SMALL_SIGNIFICANT), "h1", None)
    assert art["rendered"]["verdict"]["status"] == "not_material"


def test_a_stored_verdict_is_recomputed_and_non_causal_records_are_left_alone():
    # BUG-549: ``rendered`` is unsigned, so a stored verdict is replaced by the one
    # the signed estimates give; the rest of the rendered block is kept.
    rendered = {"verdict": {"status": "detected", "label": "x"}, "headline": "h"}
    art = {**_stored(CURRENT_RULE, []), "rendered": rendered}
    out = cf_main._artifact_for_caller(art, "h1", None)["rendered"]
    assert out["verdict"]["status"] == "empty" and out["headline"] == "h"
    financial = {"record_id": "f1", "findings": [], "rendered": {}}
    assert cf_main._artifact_for_caller(financial, "f1", None) == financial
