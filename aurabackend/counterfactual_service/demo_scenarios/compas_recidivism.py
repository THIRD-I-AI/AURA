"""COMPAS recidivism — racial-disparity audit (real, famous dataset).

Unlike the other demo scenarios (seeded synthetic data with a planted effect),
this one audits the **real, public ProPublica COMPAS dataset** — the Broward
County two-year-recidivism data behind the 2016 "Machine Bias" investigation.
We use the standard ProPublica preprocessing filtered to the two largest groups
(African-American vs Caucasian), de-identified to four columns; no PII beyond
what ProPublica already released.

HONEST FRAMING — read this before quoting the result. The audited question is:
*does race causally affect two-year recidivism after adjusting for prior-offense
count and age?* This is the **outcome-disparity** question. It is NOT ProPublica's
headline claim, which was about the COMPAS **risk score's** error-rate imbalance
(false-positive rates by race) — a different analysis on a different target. The
honest, defensible result here is that the large *raw* recidivism gap (~+13pp)
**largely attenuates** once you adjust for prior offenses and age (the estimators'
95% CIs straddle zero), i.e. most of the outcome gap is explained by the adjusted
factors rather than a direct race effect. The certificate's verdict and sensitivity
report this faithfully; the narrative must not be quoted as "COMPAS is biased."
"""
from __future__ import annotations

import pathlib

import pandas as pd

from ..schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from .base import DemoScenario, register

_DATA = pathlib.Path(__file__).parent / "data" / "compas_two_year.csv"


class CompasRecidivismScenario(DemoScenario):
    id = "compas_recidivism"
    title = "COMPAS Recidivism — Racial Disparity Audit"
    vertical = "criminal justice"
    description = (
        "On the real ProPublica COMPAS data: does race causally affect two-year "
        "recidivism after adjusting for prior offenses and age? Shows how much of "
        "the raw racial gap survives causal adjustment — signed and verifiable."
    )
    instrument = None

    def build_dataset(self) -> pd.DataFrame:
        # Static, de-identified public subset (deterministic — same bytes every run).
        return pd.read_csv(_DATA)

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question=(
                "Does race causally affect two-year recidivism after adjusting "
                "for prior-offense count and age?"
            ),
            treatment=InterventionSpec(column="african_american", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="two_year_recid", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                # priors_count and age are the adjusted confounders (backdoor set).
                ("priors_count", "african_american"),
                ("priors_count", "two_year_recid"),
                ("age", "african_american"),
                ("age", "two_year_recid"),
                ("african_american", "two_year_recid"),  # the effect audited
            ]),
            dataset=DatasetRef(source_id="demo:compas_recidivism"),
            audience="auditor",
        )

    def narrative(self, artifact: dict) -> str:
        ests = [e for e in artifact.get("estimates", []) if e.get("error") is None]
        if not ests:
            return "Audit did not produce a usable estimate."
        pts = [float(e["point"]) for e in ests]
        avg = sum(pts) / len(pts)
        # Significant only if every estimator's CI excludes zero (mirrors the
        # significance-aware verdict — never overclaim on a CI that crosses zero).
        def excludes_zero(e: dict) -> bool:
            lo, hi = float(e["ci_lower"]), float(e["ci_upper"])
            return (lo > 0 and hi > 0) or (lo < 0 and hi < 0)
        significant = all(excludes_zero(e) for e in ests)
        raw_gap = 0.13  # +13pp unadjusted AA-vs-Caucasian recidivism rate gap
        if significant:
            return (
                f"After adjusting for prior offenses and age, a statistically "
                f"significant racial difference of about {abs(avg):.0%} in two-year "
                f"recidivism remains across {len(ests)} estimators."
            )
        return (
            f"The raw two-year-recidivism gap between groups is about {raw_gap:.0%}, "
            f"but after adjusting for prior offenses and age the causal estimate is "
            f"~{abs(avg):.0%} and not statistically significant (95% intervals include "
            f"zero) across {len(ests)} estimators — most of the raw gap is explained by "
            f"the adjusted factors rather than a direct race effect. This audits the "
            f"recidivism outcome, not the COMPAS score's error-rate balance."
        )


register(CompasRecidivismScenario())
