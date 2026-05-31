"""Healthcare prior-authorization audit (YC demo scenario #3).

Same examiner / reviewer-leniency instrumental-variables identification
as the other scenarios, in the healthcare-admin vertical: the audited
treatment is a *mutable* utilization-review decision — a request being
``flagged_nonurgent`` — not the patient's immutable protected class.
Prior-auth reviewers vary in how readily they flag
(``reviewer_assignment`` is the quasi-random instrument), shifting the
flag without affecting the final authorization except through it.

A latent ``u`` (unobserved clinical complexity) drives BOTH the flag and
the denial, so a naive/backdoor estimate is endogenous — IV (reviewer
leniency) recovers the true effect. Protected class enters as the
disparate-impact lens: it raises the flag rate, so the harm falls
disproportionately on that group.
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

_SEED = 31023
_N = 800


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class HealthcarePriorAuthScenario(DemoScenario):
    id = "healthcare_prior_auth"
    title = "Healthcare Prior-Authorization Audit"
    vertical = "healthcare"
    description = (
        "Did a 'non-urgent' utilization-review flag — which disproportionately "
        "hits the protected class — causally lower prior-auth approval? Identified "
        "via reviewer-leniency instrumental variables."
    )
    instrument = "reviewer_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n).astype(float)
        u = rng.normal(0.0, 1.0, n)                          # unobserved clinical complexity
        severity = (5.0 - 0.6 * protected + 0.9 * u + rng.normal(0, 1.4, n)).clip(0, 10).round(1)
        prior_denials = (0.8 + 0.4 * protected + 0.5 * u + rng.normal(0, 0.5, n)).clip(0, 10).round()
        age = (52 + rng.normal(0, 16, n)).clip(18, 95).round()
        reviewer = rng.integers(0, 2, n).astype(float)       # instrument (leniency)

        # Treatment: the mutable 'non-urgent' flag. Lower severity, more prior
        # denials, the protected class, the latent u, and reviewer strictness
        # all raise the flag rate.
        flag_logit = (
            -0.3
            - 0.30 * (severity - 5.0)
            + 0.45 * prior_denials
            + 0.50 * protected
            + 0.70 * u
            + 1.20 * reviewer
        )
        flagged_nonurgent = (rng.random(n) < _sigmoid(flag_logit)).astype(float)

        # Outcome: prior-auth approval. The flag causally lowers approval (audited
        # effect); u also lowers it (endogeneity); reviewer does NOT enter
        # directly (exclusion); protected only via the flag.
        approve_logit = (
            1.1
            + 0.35 * (severity - 5.0)
            - 0.20 * prior_denials
            - 0.004 * (age - 52)
            - 2.0 * flagged_nonurgent
            - 0.9 * u
        )
        auth_approved = (rng.random(n) < _sigmoid(approve_logit)).astype(float)

        return pd.DataFrame({
            "protected_class": protected,
            "severity": severity,
            "prior_denials": prior_denials,
            "age": age,
            "reviewer_assignment": reviewer,
            "flagged_nonurgent": flagged_nonurgent,
            "auth_approved": auth_approved,
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question=(
                "Did a non-urgent flag causally lower prior-auth approval, "
                "instrumented by reviewer leniency?"
            ),
            treatment=InterventionSpec(column="flagged_nonurgent", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="auth_approved", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("severity", "flagged_nonurgent"),
                ("severity", "auth_approved"),
                ("prior_denials", "flagged_nonurgent"),
                ("prior_denials", "auth_approved"),
                ("age", "auth_approved"),
                ("protected_class", "flagged_nonurgent"),
                ("reviewer_assignment", "flagged_nonurgent"),  # instrument -> treatment
                ("flagged_nonurgent", "auth_approved"),         # the effect audited
            ]),
            dataset=DatasetRef(source_id="demo:healthcare_prior_auth"),
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
            f"Across {len(ests)} estimators, a non-urgent flag {direction} prior-auth "
            f"approval by {abs(avg):.0%} on average. Because the protected class is "
            f"flagged more often, that harm falls disproportionately on them — a "
            f"disparate impact a raw approval-rate comparison would miss."
        )


register(HealthcarePriorAuthScenario())
