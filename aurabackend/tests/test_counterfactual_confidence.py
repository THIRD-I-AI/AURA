"""Pure-function tests for the deterministic confidence scorer."""
from __future__ import annotations

import pytest

from counterfactual_service.engine import (
    pairwise_ci_overlap_rate,
    score_confidence,
)
from counterfactual_service.schemas import (
    AdversarialChallenge,
    CounterfactualEstimate,
    RefutationResult,
)


def _est(method, point, lo, hi, error=None):
    return CounterfactualEstimate(
        method=method, point=point, ci_lower=lo, ci_upper=hi,
        n_samples=100, error=error,
    )


def _refute(refuter, passed):
    return RefutationResult(refuter=refuter, passed=passed)


def _ch(severity, text="t"):
    return AdversarialChallenge(text=text, severity=severity)


# ── pairwise_ci_overlap_rate ─────────────────────────────────────────

def test_overlap_rate_zero_estimates():
    assert pairwise_ci_overlap_rate([]) == 0.0


def test_overlap_rate_single_estimate():
    assert pairwise_ci_overlap_rate([_est("ipw", 1, 0, 2)]) == 1.0


def test_overlap_rate_full_overlap():
    rate = pairwise_ci_overlap_rate([
        _est("ipw", 1.5, 1.0, 2.0),
        _est("psm", 1.6, 1.1, 2.1),
        _est("linear_regression", 1.5, 1.2, 1.8),
    ])
    assert rate == 1.0


def test_overlap_rate_no_overlap():
    rate = pairwise_ci_overlap_rate([
        _est("ipw", 1.5, 1.0, 2.0),
        _est("psm", 5.0, 4.0, 6.0),
    ])
    assert rate == 0.0


def test_overlap_rate_excludes_errored_estimates():
    rate = pairwise_ci_overlap_rate([
        _est("ipw", 1.5, 1.0, 2.0),
        _est("psm", 1.6, 1.1, 2.1),
        _est("double_ml", 0, 0, 0, error="boom"),
    ])
    # only the two valid estimates contribute → one pair, fully overlapping
    assert rate == 1.0


# ── score_confidence golden table ────────────────────────────────────

@pytest.mark.parametrize(
    "estimates, refutations, challenges, expected",
    [
        # All refuters pass, CIs overlap, no high-severity → high
        ([_est("ipw", 1, 0, 2), _est("psm", 1, 0, 2)],
         [_refute("placebo", True), _refute("data_subset", True)],
         [], "high"),
        # Half refuters pass, no CI overlap, no high-severity
        # raw = 0.5*0.5 + 0.4*0 - 0 = 0.25 → low
        ([_est("ipw", 1, 0, 2), _est("psm", 5, 4, 6)],
         [_refute("placebo", True), _refute("data_subset", False)],
         [], "low"),
        # All pass, all overlap, but two high-severity challenges
        # raw = 0.5*1 + 0.4*1 - 0.3*2 = 0.3 → low
        ([_est("ipw", 1, 0, 2), _est("psm", 1, 0, 2)],
         [_refute("placebo", True), _refute("data_subset", True)],
         [_ch("high"), _ch("high")], "low"),
        # No refutations is legal but kills the trust signal → low
        ([_est("ipw", 1, 0, 2)], [], [], "low"),
        # All refute pass, single estimate (ci_overlap=1.0), no challenges
        # raw = 0.5*1 + 0.4*1 - 0 = 0.9 → high
        ([_est("ipw", 1, 0, 2)],
         [_refute("placebo", True), _refute("data_subset", True)],
         [], "high"),
        # Half pass, full overlap, no challenges
        # raw = 0.5*0.5 + 0.4*1 - 0 = 0.65 → medium
        ([_est("ipw", 1, 0, 2), _est("psm", 1, 0, 2)],
         [_refute("placebo", True), _refute("data_subset", False)],
         [], "medium"),
    ],
)
def test_confidence_table(estimates, refutations, challenges, expected):
    assert score_confidence(estimates, refutations, challenges) == expected
