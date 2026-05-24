"""
Sprint S23 — E-value and partial-R² robustness sensitivity analysis.

Two post-hoc robustness metrics are computed for every successful
estimator in a CounterfactualArtifact:

1. **E-value** (VanderWeele & Ding 2017, Epidemiology 28(6)):
   The minimum strength of association (as a risk ratio) that an
   unmeasured confounder would need to have with BOTH the treatment
   AND the outcome to fully explain away the observed ATE.

   E-value = 1 means the null is already plausible without any
   confounding. E-value = 4 means a confounder must be at least 4×
   more common in the treated arm AND drive the outcome by 4× to
   nullify the effect.

   For continuous outcomes we use the approximation from VanderWeele
   (2021, AJE): RR ≈ exp(0.91 × |d|) where d = ATE / SD_Y is the
   standardised mean difference. The 0.91 constant comes from
   equating the normal-approximation RR with the log-normal reference
   distribution most commonly assumed in observational epidemiology.

2. **Partial-R² robustness value** (Cinelli & Hazlett 2020, JRSS-B 82(1)):
   The minimum partial R² that an unmeasured confounder would need
   with BOTH treatment AND outcome (after observed covariates) to
   drive the estimated effect to zero. Derived from the t-statistic
   t = ATE / SE and degrees of freedom df ≈ n − 2:

     RV = t² / (t² + df)

   A value of 0.02 is fragile (2% residual variance suffices);
   0.30 means the confounder must dominate both margins.

Both metrics are computed in ``sensitivity_analysis()`` which the
engine calls AFTER signing so that these advisory fields never enter
the audit hash basis.

Anchors
-------
* VanderWeele, T.J., & Ding, P. (2017). Sensitivity analysis in
  observational research: introducing the E-value. Ann Intern Med
  167(4), 268-274.
* VanderWeele, T.J. (2021). Selecting a scale for expressing
  causal effects: sensitivity analysis for continuous outcomes.
  Am J Epidemiol 190(11), 2294-2300.
* Cinelli, C., & Hazlett, C. (2020). Making sense of sensitivity:
  extending omitted variable bias. JRSS-B 82(1), 39-67.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .schemas import CounterfactualEstimate, SensitivityReport


def _standardized_rr(d: float) -> float:
    """Approximate RR from a standardised continuous ATE (VanderWeele 2021).

    d = ATE / SD_Y; caller must pass abs(d).
    Clamped at d=700 to stay within float range (astronomically large RR).
    """
    # Cap at 370 so rr² = exp(2*0.91*370) = exp(673.4) stays below float_max
    # (ln(float_max) ≈ 709.78). Beyond this threshold the E-value is
    # astronomically large and practically equivalent.
    return math.exp(0.91 * min(abs(d), 370.0))


def _evalue_from_rr(rr: float) -> float:
    """E-value formula (VanderWeele & Ding 2017, eq. 3).

    rr >= 1 is required; for RR < 1 caller should first invert.
    """
    if rr <= 1.0:
        return 1.0
    return rr + math.sqrt(rr * (rr - 1.0))


def compute_evalue(
    point: float,
    ci_lower: float,
    ci_upper: float,
    sd_outcome: float,
) -> dict:
    """E-value for a continuous ATE (VanderWeele & Ding 2017).

    Args:
        point: ATE point estimate.
        ci_lower, ci_upper: 95% confidence interval.
        sd_outcome: standard deviation of the outcome column; used to
            standardise the ATE. Must be > 0.

    Returns a dict with:
        evalue     — E-value at the point estimate.
        evalue_ci  — E-value at the CI bound closer to null; equals 1.0
                     when the CI already contains zero (the effect is
                     compatible with the null without any confounding).
        rr_approx  — intermediate approximate risk ratio at the point.
    """
    sd = max(abs(sd_outcome), 1e-9)
    d_point = abs(point) / sd
    rr = _standardized_rr(d_point)
    ev = _evalue_from_rr(rr)

    # CI bound closer to null (the conservative end for E-value reporting).
    ci_null_bound = ci_lower if point >= 0 else ci_upper
    ci_crosses_null = (point >= 0 and ci_null_bound <= 0) or (
        point < 0 and ci_null_bound >= 0
    )
    if ci_crosses_null:
        ev_ci = 1.0
    else:
        rr_ci = _standardized_rr(abs(ci_null_bound) / sd)
        ev_ci = _evalue_from_rr(rr_ci)

    return {
        "evalue": round(ev, 4),
        "evalue_ci": round(ev_ci, 4),
        "rr_approx": round(rr, 4),
    }


def compute_robustness_value(
    point: float,
    ci_lower: float,
    ci_upper: float,
    n_samples: int,
) -> Optional[float]:
    """Partial-R² robustness value (Cinelli & Hazlett 2020).

    RV = t² / (t² + df)  where  t = point / SE,  SE = (ci_upper - ci_lower) / (2 × 1.96),
    df ≈ n_samples − 2.

    Returns None when the SE is degenerate (zero-width CI) or n_samples < 3,
    since the formula is undefined in those cases.
    """
    if n_samples < 3:
        return None
    ci_width = ci_upper - ci_lower
    if ci_width <= 1e-12:
        return None
    se = ci_width / (2.0 * 1.96)
    t_stat = point / se
    f_stat = t_stat ** 2
    df = max(n_samples - 2, 1)
    rv = f_stat / (f_stat + df)
    return round(rv, 4)


def _interpret(evalue: float, evalue_ci: float, rv: Optional[float]) -> str:
    if evalue_ci <= 1.0:
        return (
            "The confidence interval includes the null; the finding is not "
            "statistically significant. No unmeasured confounding is needed "
            "to make the estimate consistent with zero effect (E-value CI = 1.00)."
        )
    lines = [
        f"An unmeasured confounder would need to be associated with both "
        f"treatment and outcome by a factor of at least {evalue:.2f}× "
        f"(approximate risk-ratio scale) to fully explain the point estimate away, "
        f"and {evalue_ci:.2f}× to shift the confidence interval to the null."
    ]
    if rv is not None:
        pct = round(rv * 100, 1)
        lines.append(
            f"Under the Cinelli-Hazlett partial-R² framework, a confounder "
            f"accounting for at least {pct}% of residual variance in both treatment "
            f"and outcome would suffice to reduce the estimated effect to zero "
            f"(robustness value = {rv:.4f})."
        )
    return " ".join(lines)


def sensitivity_analysis(
    estimates: "List[CounterfactualEstimate]",
    sd_outcome: float,
) -> "List[SensitivityReport]":
    """Compute a SensitivityReport for each successful estimate.

    Failed estimators (``error is not None``) are skipped — there is
    no point estimate to evaluate.
    """
    from .schemas import SensitivityReport

    reports: List[SensitivityReport] = []
    for est in estimates:
        if est.error is not None:
            continue
        ev = compute_evalue(est.point, est.ci_lower, est.ci_upper, sd_outcome)
        rv = compute_robustness_value(
            est.point, est.ci_lower, est.ci_upper, est.n_samples
        )
        reports.append(
            SensitivityReport(
                method=est.method,
                evalue=ev["evalue"],
                evalue_ci=ev["evalue_ci"],
                robustness_value=rv,
                sd_outcome=round(float(sd_outcome), 4),
                interpretation=_interpret(ev["evalue"], ev["evalue_ci"], rv),
            )
        )
    return reports


__all__ = [
    "compute_evalue",
    "compute_robustness_value",
    "sensitivity_analysis",
]
