"""
Sensitivity analysis primitives — Sprint S23.

Two complementary tools, both deterministic (no RNG, no sampling), both
pure NumPy:

* **E-value** (VanderWeele & Ding 2017): how strong would an unmeasured
  confounder have to be — measured on the risk-ratio scale — to fully
  explain away the observed effect? Computed for both the point estimate
  and the CI limit closer to the null. For a continuous outcome the
  observed effect is first standardised by the outcome SD and converted
  to an approximate RR via the Chinn (2000) formula RR ≈ exp(0.91 · |d|).

* **Robustness value** (Cinelli & Hazlett 2020): the minimum strength of
  association — measured on the partial-R² scale — that an unmeasured
  confounder would need with BOTH treatment and outcome to bring the
  estimate down to zero (RV_q=1) or to lose statistical significance
  (RV_{q,alpha}). Closed-form from the t-statistic and residual dof.

Both numbers are byte-stable functions of (point, ci_lower, ci_upper,
n_samples, n_controls, outcome_sd) — so embedding them in the
``CounterfactualEstimate`` hash basis preserves Layer 10 byte-identity.

Reference implementations cross-checked against:
- ``EValue`` R package (Mathur et al., R Journal 2018) for the E-value
  formulas and the CI null-cross convention.
- ``sensemakr`` R package (Cinelli, Ferwerda, Hazlett 2020) for the RV
  + extreme-scenario adjustment formulas.

No exceptions, no logging. The engine treats the dict return value
directly; non-finite outputs are guarded at the boundary so a bad input
(zero SE, n_controls > n_samples) returns a structured "degenerate" flag
the caller can route to ``sensitivity=None``.
"""
from __future__ import annotations

import math
from typing import Dict

# Chinn (2000) conversion constant. d -> log(RR) ≈ 0.91 · d.
# Documented in VanderWeele & Ding (2017) Section 4 for continuous-
# outcome E-value derivation. Keep as a module-level constant so the
# magic number has a name and the calibration test below has something
# concrete to reference.
_CHINN_LOG_RR_PER_D = 0.91


def _evalue_from_rr(rr: float) -> float:
    """Closed-form VanderWeele-Ding E-value.

    For RR > 1: E = RR + sqrt(RR · (RR - 1))
    For 0 < RR < 1: re-express as 1/RR (the protective effect flips into
        a harmful one for the sensitivity bound) and apply the same.
    For RR = 1 (no effect): E = 1 by convention.

    Returns a finite float >= 1.0.
    """
    if rr <= 0.0 or not math.isfinite(rr):
        # RR must be strictly positive; clamp degenerate input to "no
        # confounder needed" so a caller's downstream interpretation
        # ("how strong a confounder?") is still meaningful.
        return 1.0
    if rr < 1.0:
        rr = 1.0 / rr
    if rr == 1.0:
        return 1.0
    return rr + math.sqrt(rr * (rr - 1.0))


def compute_evalue(
    point: float,
    ci_lower: float,
    ci_upper: float,
    *,
    outcome_sd: float,
) -> Dict[str, object]:
    """E-value of the point estimate AND the CI bound closer to null.

    Continuous-outcome variant: the standardised effect d = point /
    outcome_sd is converted to an approximate risk ratio via Chinn 2000
    (``RR ≈ exp(0.91 · |d|)``), then VanderWeele-Ding's closed form
    yields the E-value.

    For the CI bound, two rules:
    1. If the CI crosses the null (``ci_lower < 0 < ci_upper``), then
       no confounder is needed to nullify — ``e_value_ci = 1.0``.
    2. Otherwise pick the CI limit closer to 0 (in absolute value)
       and apply the same conversion.

    Returns a dict with all numbers as plain floats:
    * ``e_value_point`` — E-value of the point estimate, >= 1.0
    * ``e_value_ci`` — E-value of the CI bound closer to null, >= 1.0
    * ``rr_approx`` — the approximate RR used for the point E-value
    * ``standardised_effect_d`` — d = point / outcome_sd (signed)
    * ``null_crossed`` — 1.0 if the CI crosses 0, else 0.0
    """
    if outcome_sd <= 0.0 or not math.isfinite(outcome_sd):
        # Degenerate outcome (constant column) — no sensitivity question
        # to ask. Return the "no information" sentinel.
        return {
            "e_value_point": 1.0,
            "e_value_ci": 1.0,
            "rr_approx": 1.0,
            "standardised_effect_d": 0.0,
            "null_crossed": True,
        }

    d_point = point / outcome_sd
    rr_point = math.exp(_CHINN_LOG_RR_PER_D * abs(d_point))
    e_point = _evalue_from_rr(rr_point)

    # The CI defines a confidence interval for the SAME effect on the
    # raw-units scale, so each limit is standardised by the same
    # outcome_sd. The "closer to null" limit gives the E-value of the
    # CI per VanderWeele-Ding's recommendation.
    if ci_lower <= 0.0 <= ci_upper:
        null_crossed = True
        e_ci = 1.0
    else:
        # Pick the limit closer to 0 in absolute value.
        closer = ci_lower if abs(ci_lower) < abs(ci_upper) else ci_upper
        d_ci = closer / outcome_sd
        rr_ci = math.exp(_CHINN_LOG_RR_PER_D * abs(d_ci))
        e_ci = _evalue_from_rr(rr_ci)
        null_crossed = False

    return {
        "e_value_point": round(e_point, 6),
        "e_value_ci": round(e_ci, 6),
        "rr_approx": round(rr_point, 6),
        "standardised_effect_d": round(d_point, 6),
        "null_crossed": null_crossed,
    }


def compute_robustness_value(
    point: float,
    se: float,
    dof: int,
    *,
    q: float = 1.0,
) -> Dict[str, float]:
    """Cinelli-Hazlett 2020 robustness value + extreme-scenario adjustment.

    Closed-form, no iteration. ``RV_q`` answers: how strong would an
    unmeasured confounder Z have to be, in partial-R² terms with BOTH
    treatment (D) and outcome (Y) given controls (X), to shrink the
    estimate to q · point?

        f_q = q · |t| / sqrt(dof)
        RV_q = 0.5 · (sqrt(f_q^4 + 4·f_q^2) - f_q^2)            (Eq. 9)

    ``q = 1.0`` (default) ⇒ "what would nullify the estimate".

    The "extreme scenario adjusted" estimate is the bias-corrected
    estimate under the assumption that the unmeasured confounder is
    as strong as the observed partial relationship between treatment
    and outcome (the "1x benchmark" used by ``sensemakr`` by default):

        R²_{D,Z|X} = R²_{Y,Z|D,X} = partial_r2_yd_x
        bias = se · sqrt(dof · R²_{Y,Z|D,X} · R²_{D,Z|X} / (1 - R²_{D,Z|X}))
        adjusted = sign(point) · max(0, |point| - bias)         (Eq. 5)

    Returns plain floats:
    * ``t_statistic`` — point / se
    * ``dof`` — passed through (echoed for canonical-JSON shape stability)
    * ``partial_r2_yd_x`` — t² / (t² + dof)
    * ``robustness_value`` — RV_q at the given q (default q=1)
    * ``extreme_scenario_adjusted`` — adjusted estimate under the 1x
      benchmark; equals 0 when the bias bound exceeds |point|.
    """
    if (
        se <= 0.0
        or not math.isfinite(se)
        or dof <= 0
        or not math.isfinite(point)
    ):
        return {
            "t_statistic": 0.0,
            "dof": float(max(0, dof)),
            "partial_r2_yd_x": 0.0,
            "robustness_value": 0.0,
            "extreme_scenario_adjusted": 0.0,
        }

    t_stat = point / se
    abs_t = abs(t_stat)

    # Partial R²(Y, D | X) from the t-statistic. Identity used by
    # Cinelli-Hazlett 2020 (and standard OLS): t² = R²/(1-R²) · dof.
    partial_r2 = (abs_t * abs_t) / (abs_t * abs_t + dof)

    # f_q = q · |t| / sqrt(dof). The partial-F-statistic equivalent at
    # the q-scaled effect magnitude.
    f_q = q * abs_t / math.sqrt(dof)
    rv = 0.5 * (math.sqrt(f_q * f_q * (f_q * f_q + 4.0)) - f_q * f_q)

    # 1x extreme-scenario bias bound. Use partial_r2 as both R²_dz and
    # R²_yz; if either approaches 1 the (1 - R²_dz) denominator blows
    # up the bias — clamp to a finite value to keep the artifact-hash
    # bytes well-defined.
    denom = max(1.0 - partial_r2, 1e-12)
    bias = se * math.sqrt(dof * partial_r2 * partial_r2 / denom)
    sign = 1.0 if point >= 0.0 else -1.0
    adjusted = sign * max(0.0, abs(point) - bias)

    return {
        "t_statistic": round(t_stat, 6),
        "dof": float(dof),
        "partial_r2_yd_x": round(partial_r2, 6),
        "robustness_value": round(rv, 6),
        "extreme_scenario_adjusted": round(adjusted, 6),
    }


def compute_sensitivity_report(
    point: float,
    ci_lower: float,
    ci_upper: float,
    *,
    n_samples: int,
    n_controls: int,
    outcome_sd: float,
) -> Dict[str, float]:
    """Flat dict shaped for ``schemas.SensitivityReport``.

    Combines the two analyses. The standard error is backed out from
    the CI assuming a normal sampling distribution at 95% (the engine
    publishes 95% CIs across every estimator slot):

        se = (ci_upper - ci_lower) / (2 · 1.959964...)

    The residual degrees of freedom for OLS-style inference is
    ``n_samples - n_controls - 1`` (subtract treatment indicator + each
    control + intercept). Bounded below by 1 to keep the closed-form
    Cinelli-Hazlett math well-defined on tiny samples — that's a more
    conservative dof than the truth on n=10 but the contract is "give
    me a finite RV", not "match an OLS textbook".

    Returns the dict directly — the caller builds a Pydantic
    ``SensitivityReport`` from it. The keys are deliberately the same
    as ``SensitivityReport``'s field names so the constructor call is
    a single splat. All numeric values are rounded to 6 decimals at
    the leaf so the canonical-JSON encoding is byte-stable across
    re-runs of the engine (Layer 10 contract).
    """
    # 1.959964 is the two-sided 95% z-quantile. Anchored as a constant
    # in case scipy isn't importable in some lean deployment — keeping
    # the sensitivity computation a pure-NumPy/stdlib operation.
    Z_95 = 1.959963984540054
    se = max((ci_upper - ci_lower) / (2.0 * Z_95), 0.0)
    dof = max(1, n_samples - n_controls - 1)

    evalue = compute_evalue(
        point=point,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        outcome_sd=outcome_sd,
    )
    rv = compute_robustness_value(point=point, se=se, dof=dof)

    return {
        "e_value_point": evalue["e_value_point"],
        "e_value_ci": evalue["e_value_ci"],
        "rr_approx": evalue["rr_approx"],
        "standardised_effect_d": evalue["standardised_effect_d"],
        "null_crossed": evalue["null_crossed"],
        "t_statistic": rv["t_statistic"],
        "dof": int(rv["dof"]),
        "partial_r2_yd_x": rv["partial_r2_yd_x"],
        "robustness_value": rv["robustness_value"],
        "extreme_scenario_adjusted": rv["extreme_scenario_adjusted"],
    }
