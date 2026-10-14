"""BUG-402: materiality is judged against the outcome's scale, and signs do not cancel."""
from __future__ import annotations

from counterfactual_service.verdict import significance_verdict


def _est(point, lo, hi, outcome_sd=None):
    est = {"point": point, "ci_lower": lo, "ci_upper": hi, "error": None}
    if outcome_sd is not None:
        est["sensitivity"] = {"standardised_effect_d": point / outcome_sd}
    return est


def test_a_150_basis_point_effect_on_a_decimal_rate_is_detected():
    # interest_rate stored as a decimal: sd about 0.01, effect +0.015.
    estimates = [_est(0.015, 0.012, 0.018, outcome_sd=0.01), _est(0.015, 0.012, 0.018, outcome_sd=0.01)]
    assert significance_verdict(estimates)["status"] == "detected"


def test_a_five_cent_effect_on_a_dollar_amount_is_not_material():
    # loan amount in dollars: sd about 20,000, effect +0.05.
    estimates = [_est(0.05, 0.01, 0.09, outcome_sd=20_000.0)]
    assert significance_verdict(estimates)["status"] == "not_material"


def test_a_two_point_gap_in_an_approval_rate_is_still_material():
    # binary outcome near 50%: sd 0.5, effect 0.03 -> d = 0.06.
    estimates = [_est(0.03, 0.01, 0.05, outcome_sd=0.5)]
    assert significance_verdict(estimates)["status"] == "detected"


def test_opposite_signs_do_not_cancel_to_not_material():
    estimates = [_est(0.10, 0.06, 0.14, outcome_sd=0.5), _est(-0.10, -0.14, -0.06, outcome_sd=0.5)]
    verdict = significance_verdict(estimates)
    assert verdict["status"] == "inconsistent"
    assert "disagree on the direction" in verdict["label"]


def test_an_interval_that_includes_zero_is_not_significant():
    estimates = [_est(0.10, -0.02, 0.22, outcome_sd=0.5), _est(0.10, 0.06, 0.14, outcome_sd=0.5)]
    assert significance_verdict(estimates)["status"] == "not_significant"


def test_without_a_standardised_effect_the_absolute_cutoff_still_applies():
    assert significance_verdict([_est(0.01, 0.005, 0.015)])["status"] == "not_material"
    assert significance_verdict([_est(0.05, 0.03, 0.07)])["status"] == "detected"
    # d is reported as 0 for a constant outcome; that is "unknown", not "no effect".
    constant = {"point": 0.05, "ci_lower": 0.03, "ci_upper": 0.07, "error": None,
                "sensitivity": {"standardised_effect_d": 0.0}}
    assert significance_verdict([constant])["status"] == "detected"
