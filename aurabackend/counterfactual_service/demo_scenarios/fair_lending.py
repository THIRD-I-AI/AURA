"""Fair-lending credit-decision audit (YC demo scenario #1)."""
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
_N = 600


class FairLendingScenario(DemoScenario):
    id = "fair_lending"
    title = "Fair-Lending Credit Decision Audit"
    vertical = "compliance"
    description = (
        "Did an applicant's protected class causally affect loan approval, "
        "holding creditworthiness fixed? (ECOA / fair-lending)"
    )
    instrument = "officer_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n)                       # treatment (0/1)
        # Confounder correlated with protected class AND approval.
        credit_score = (680 - 25 * protected + rng.normal(0, 40, n)).clip(300, 850)
        income = (60000 - 5000 * protected + rng.normal(0, 12000, n)).clip(15000, None)
        dti = (0.30 + 0.04 * protected + rng.normal(0, 0.06, n)).clip(0.0, 0.9)
        officer = rng.integers(0, 2, n)                          # instrument (leniency)
        # Planted causal structure: creditworthiness drives approval; a
        # modest DIRECT protected-class effect (-0.12 on the logit) is the
        # disparate impact the audit must recover; officer leniency shifts
        # approval but is independent of creditworthiness (valid instrument).
        logit = (
            -3.5
            + 0.006 * (credit_score - 680)
            + 0.000015 * (income - 60000)
            - 1.5 * (dti - 0.30)
            - 0.12 * protected
            + 0.8 * officer
        )
        p = 1.0 / (1.0 + np.exp(-logit))
        approved = (rng.random(n) < p).astype(int)
        return pd.DataFrame({
            "protected_class": protected.astype(float),
            "credit_score": credit_score.round(1),
            "income": income.round(2),
            "dti": dti.round(4),
            "officer_assignment": officer.astype(float),
            "approved": approved.astype(float),
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question="Did protected class causally affect approval, holding creditworthiness fixed?",
            treatment=InterventionSpec(column="protected_class", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="approved", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("credit_score", "protected_class"),
                ("credit_score", "approved"),
                ("income", "approved"),
                ("dti", "approved"),
                ("protected_class", "approved"),
                ("officer_assignment", "protected_class"),  # instrument -> treatment
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
            f"Across {len(ests)} estimators, belonging to the protected class "
            f"{direction} the probability of approval by {abs(avg):.1%} on average, "
            f"after adjusting for creditworthiness — the disparate impact a raw "
            f"approval-rate comparison would misstate."
        )


register(FairLendingScenario())
