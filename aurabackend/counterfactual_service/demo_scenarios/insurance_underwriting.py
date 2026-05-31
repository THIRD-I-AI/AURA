"""Insurance underwriting audit (YC demo scenario #2).

Same examiner / leniency instrumental-variables identification as
``fair_lending`` (the design that makes IV valid), in the insurance
vertical: the audited treatment is a *mutable* underwriting decision —
being routed to a ``high_risk_tier`` — not the policyholder's immutable
protected class. Claims adjusters vary in how readily they up-tier
(``adjuster_assignment`` is the quasi-random instrument), shifting the
tier without affecting the final claim decision except through it.

A latent ``u`` (unobserved loss propensity) drives BOTH the tiering and
the claim denial, so a naive/backdoor estimate is endogenous — IV
(adjuster leniency) recovers the true effect. Protected class enters as
the disparate-impact lens: it raises the high-risk-tier rate, so the
harm of up-tiering falls disproportionately on that group.
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

_SEED = 31022
_N = 800


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class InsuranceUnderwritingScenario(DemoScenario):
    id = "insurance_underwriting"
    title = "Insurance Underwriting Audit"
    vertical = "insurance"
    description = (
        "Did a 'high-risk tier' underwriting assignment — which disproportionately "
        "hits the protected class — causally lower claim approval? Identified via "
        "adjuster-leniency instrumental variables."
    )
    instrument = "adjuster_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n).astype(float)
        u = rng.normal(0.0, 1.0, n)                          # unobserved loss propensity
        risk_score = (640 - 22 * protected - 24 * u + rng.normal(0, 38, n)).clip(300, 850)
        prior_claims = (1.2 + 0.5 * protected + 0.6 * u + rng.normal(0, 0.6, n)).clip(0, 12).round()
        vehicle_age = (7.0 + rng.normal(0, 3.5, n)).clip(0, 30).round(1)
        adjuster = rng.integers(0, 2, n).astype(float)       # instrument (leniency)

        # Treatment: the mutable high-risk-tier assignment.
        tier_logit = (
            -0.5
            - 0.011 * (risk_score - 640)
            + 0.45 * prior_claims
            + 0.50 * protected
            + 0.70 * u
            + 1.20 * adjuster
        )
        high_risk_tier = (rng.random(n) < _sigmoid(tier_logit)).astype(float)

        # Outcome: claim approval. The tier causally lowers approval (audited
        # effect); u also lowers it (endogeneity defeating backdoor adjustment);
        # adjuster does NOT enter directly (exclusion); protected only via tier.
        approve_logit = (
            1.2
            + 0.010 * (risk_score - 640)
            - 0.30 * prior_claims
            - 0.02 * (vehicle_age - 7.0)
            - 2.0 * high_risk_tier
            - 0.9 * u
        )
        claim_approved = (rng.random(n) < _sigmoid(approve_logit)).astype(float)

        return pd.DataFrame({
            "protected_class": protected,
            "risk_score": risk_score.round(1),
            "prior_claims": prior_claims,
            "vehicle_age": vehicle_age,
            "adjuster_assignment": adjuster,
            "high_risk_tier": high_risk_tier,
            "claim_approved": claim_approved,
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question=(
                "Did high-risk-tier assignment causally lower claim approval, "
                "instrumented by adjuster leniency?"
            ),
            treatment=InterventionSpec(column="high_risk_tier", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="claim_approved", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("risk_score", "high_risk_tier"),
                ("risk_score", "claim_approved"),
                ("prior_claims", "high_risk_tier"),
                ("prior_claims", "claim_approved"),
                ("vehicle_age", "claim_approved"),
                ("protected_class", "high_risk_tier"),
                ("adjuster_assignment", "high_risk_tier"),  # instrument -> treatment
                ("high_risk_tier", "claim_approved"),        # the effect audited
            ]),
            dataset=DatasetRef(source_id="demo:insurance_underwriting"),
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
            f"Across {len(ests)} estimators, a high-risk-tier assignment {direction} "
            f"claim approval by {abs(avg):.0%} on average. Because the protected class "
            f"is up-tiered more often, that harm falls disproportionately on them — a "
            f"disparate impact a raw approval-rate comparison would miss."
        )


register(InsuranceUnderwritingScenario())
