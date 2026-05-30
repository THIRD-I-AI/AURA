"""Fair-lending credit-decision audit (YC demo scenario #1).

Examiner / officer-leniency instrumental-variables design (the classic
judge-leniency identification strategy, Angrist & Krueger): the audited
treatment is a *mutable* underwriting decision — being ``flagged_high_risk``
by review — not the applicant's immutable protected class. Loan officers
vary in how readily they flag (``officer_assignment`` is the quasi-random
instrument), which shifts the flag but does not affect the final approval
except through it.

A latent ``u`` (unobserved financial instability) drives BOTH the flag and
the denial, so a naive/backdoor estimate of the flag's effect is
endogenous — IV (officer leniency) recovers the true effect that
confounder-adjustment alone cannot. Protected class enters as the
disparate-impact lens: it raises the flag rate, so the harm of flagging
falls disproportionately on the protected group.
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

_SEED = 31021
_N = 800


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class FairLendingScenario(DemoScenario):
    id = "fair_lending"
    title = "Fair-Lending Credit Decision Audit"
    vertical = "compliance"
    description = (
        "Did an underwriting 'high-risk' flag — which disproportionately hits "
        "the protected class — causally lower loan approval? Identified via "
        "officer-leniency instrumental variables (ECOA / fair-lending)."
    )
    instrument = "officer_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n).astype(float)
        u = rng.normal(0.0, 1.0, n)                         # unobserved instability
        credit_score = (700 - 25 * protected - 22 * u + rng.normal(0, 35, n)).clip(300, 850)
        income = (60000 - 5000 * protected + rng.normal(0, 12000, n)).clip(15000, None)
        dti = (0.30 + 0.04 * protected + 0.03 * u + rng.normal(0, 0.05, n)).clip(0.0, 0.9)
        officer = rng.integers(0, 2, n).astype(float)       # instrument (leniency)

        # Treatment: the mutable 'high-risk' flag. Driven by low credit, the
        # protected class (proxy bias), the unobserved u, and — crucially —
        # officer leniency (the instrument), independent of u.
        flag_logit = (
            -0.4
            - 0.010 * (credit_score - 700)
            + 0.55 * protected
            + 0.70 * u
            + 1.20 * officer
        )
        flagged = (rng.random(n) < _sigmoid(flag_logit)).astype(float)

        # Outcome: approval. The flag causally lowers approval (the effect we
        # audit); u also lowers it (endogeneity that defeats backdoor
        # adjustment); officer does NOT enter directly (exclusion), and
        # protected class enters only THROUGH the flag.
        approve_logit = (
            1.0
            + 0.011 * (credit_score - 700)
            + 0.000012 * (income - 60000)
            - 2.0 * (dti - 0.30)
            - 2.1 * flagged
            - 0.9 * u
        )
        approved = (rng.random(n) < _sigmoid(approve_logit)).astype(float)

        return pd.DataFrame({
            "protected_class": protected,
            "credit_score": credit_score.round(1),
            "income": income.round(2),
            "dti": dti.round(4),
            "officer_assignment": officer,
            "flagged_high_risk": flagged,
            "approved": approved,
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question=(
                "Did being flagged high-risk causally lower approval, "
                "instrumented by officer leniency?"
            ),
            treatment=InterventionSpec(column="flagged_high_risk", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="approved", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("credit_score", "flagged_high_risk"),
                ("credit_score", "approved"),
                ("income", "approved"),
                ("dti", "approved"),
                ("protected_class", "flagged_high_risk"),
                ("officer_assignment", "flagged_high_risk"),  # instrument -> treatment
                ("flagged_high_risk", "approved"),             # the effect audited
            ]),
            dataset=DatasetRef(source_id="demo:fair_lending"),
            audience="auditor",
        )

    def narrative(self, artifact: dict) -> str:
        ests = [e for e in artifact.get("estimates", []) if e.get("error") is None]
        if not ests:
            return "Audit did not produce a usable estimate."
        pts = [e["point"] for e in ests]
        avg = sum(pts) / len(pts)
        direction = "lowered" if avg < 0 else "raised"
        return (
            f"Across {len(ests)} estimators, a high-risk flag {direction} the "
            f"probability of approval by {abs(avg):.0%} on average. Because the "
            f"protected class is flagged more often, that harm falls "
            f"disproportionately on them — a disparate impact a raw approval-rate "
            f"comparison would miss."
        )


register(FairLendingScenario())
