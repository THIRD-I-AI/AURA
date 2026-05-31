"""Hiring fairness audit (YC demo scenario #4).

Same examiner / screener-leniency instrumental-variables identification
as the other scenarios, in the hiring vertical: the audited treatment is
a *mutable* screening decision — a candidate being ``flagged_low_fit`` by
an automated screener — not the candidate's immutable protected class.
Screening panels vary in how readily they flag (``screener_assignment``
is the quasi-random instrument), shifting the flag without affecting the
final advance decision except through it.

A latent ``u`` (unobserved candidate quality) drives BOTH the flag and
the rejection, so a naive/backdoor estimate is endogenous — IV (screener
leniency) recovers the true effect. Protected class enters as the
disparate-impact lens: it raises the flag rate (proxy discrimination),
so the harm falls disproportionately on that group (EEOC adverse impact).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from .base import DemoScenario, register

_SEED = 31024
_N = 800


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class HiringFairnessScenario(DemoScenario):
    id = "hiring_fairness"
    title = "Hiring Fairness Audit"
    vertical = "compliance"
    description = (
        "Did an automated 'low-fit' screening flag — which disproportionately "
        "hits the protected class — causally lower the chance of advancing? "
        "Identified via screener-leniency instrumental variables (EEOC adverse impact)."
    )
    instrument = "screener_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n).astype(float)
        u = rng.normal(0.0, 1.0, n)                          # unobserved candidate quality
        skill_score = (62 - 6 * protected + 18 * u + rng.normal(0, 12, n)).clip(0, 100).round(1)
        years_experience = (6.0 - 0.8 * protected + rng.normal(0, 3.0, n)).clip(0, 35).round(1)
        education_tier = rng.integers(1, 5, n).astype(float)  # 1..4
        screener = rng.integers(0, 2, n).astype(float)        # instrument (leniency)

        # Treatment: the mutable 'low-fit' flag. Lower skill, the protected
        # class, the latent u, and screener strictness all raise the flag rate.
        flag_logit = (
            -0.2
            - 0.030 * (skill_score - 62)
            - 0.05 * (years_experience - 6.0)
            + 0.50 * protected
            + 0.70 * u
            + 1.20 * screener
        )
        flagged_low_fit = (rng.random(n) < _sigmoid(flag_logit)).astype(float)

        # Outcome: advanced to interview. The flag causally lowers the chance
        # (audited effect); u also raises it (endogeneity); screener does NOT
        # enter directly (exclusion); protected only via the flag.
        advance_logit = (
            0.6
            + 0.030 * (skill_score - 62)
            + 0.06 * (years_experience - 6.0)
            + 0.10 * (education_tier - 2.5)
            - 2.0 * flagged_low_fit
            + 0.9 * u
        )
        advanced = (rng.random(n) < _sigmoid(advance_logit)).astype(float)

        return pd.DataFrame({
            "protected_class": protected,
            "skill_score": skill_score,
            "years_experience": years_experience,
            "education_tier": education_tier,
            "screener_assignment": screener,
            "flagged_low_fit": flagged_low_fit,
            "advanced": advanced,
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question=(
                "Did a low-fit screening flag causally lower the chance of "
                "advancing, instrumented by screener leniency?"
            ),
            treatment=InterventionSpec(column="flagged_low_fit", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="advanced", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("skill_score", "flagged_low_fit"),
                ("skill_score", "advanced"),
                ("years_experience", "flagged_low_fit"),
                ("years_experience", "advanced"),
                ("education_tier", "advanced"),
                ("protected_class", "flagged_low_fit"),
                ("screener_assignment", "flagged_low_fit"),  # instrument -> treatment
                ("flagged_low_fit", "advanced"),              # the effect audited
            ]),
            dataset=DatasetRef(source_id="demo:hiring_fairness"),
            audience="auditor",
        )

    def narrative(self, artifact: dict) -> str:
        ests = [e for e in artifact.get("estimates", []) if e.get("error") is None]
        if not ests:
            return "Audit did not produce a usable estimate."
        pts = [float(e["point"]) for e in ests]
        avg = sum(pts) / len(pts)
        direction = "lowered" if avg < 0 else "raised"
        return (
            f"Across {len(ests)} estimators, a low-fit flag {direction} the chance of "
            f"advancing by {abs(avg):.0%} on average. Because the protected class is "
            f"flagged more often, that adverse impact falls disproportionately on them "
            f"— a disparity a raw advance-rate comparison would miss."
        )


register(HiringFairnessScenario())
