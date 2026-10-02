"""BUG-296: the refuter pass threshold has an absolute floor of 0.1 in outcome units,
so on a rate or binary outcome a refuter passed even when the placebo effect exceeded
the real effect or the refuted estimate flipped sign."""
from __future__ import annotations

import pytest

from counterfactual_service.engine import _refuter_passed


def test_a_placebo_effect_larger_than_the_real_effect_fails():
    assert _refuter_passed("placebo", baseline=0.05, refuted=0.09) is False
    assert _refuter_passed("placebo", baseline=-0.05, refuted=0.06) is False


def test_a_placebo_effect_well_below_the_real_effect_still_passes():
    assert _refuter_passed("placebo", baseline=0.05, refuted=0.004) is True
    assert _refuter_passed("placebo", baseline=2.0, refuted=0.05) is True


@pytest.mark.parametrize("refuter", ["random_common_cause", "data_subset"])
def test_a_refuted_estimate_with_the_opposite_sign_fails(refuter):
    assert _refuter_passed(refuter, baseline=0.05, refuted=-0.04) is False
    assert _refuter_passed(refuter, baseline=-0.30, refuted=0.02) is False


@pytest.mark.parametrize("refuter", ["random_common_cause", "data_subset"])
def test_a_refuted_estimate_close_to_the_baseline_still_passes(refuter):
    assert _refuter_passed(refuter, baseline=0.05, refuted=0.045) is True
    assert _refuter_passed(refuter, baseline=2.0, refuted=1.8) is True
    assert _refuter_passed(refuter, baseline=2.0, refuted=1.0) is False
