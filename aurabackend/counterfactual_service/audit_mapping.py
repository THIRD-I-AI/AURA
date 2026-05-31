"""Turn a user column-mapping into a validated DataFrame + a causally-honest
CounterfactualQuery. Pure functions — no I/O, no engine calls — so they're
trivially testable and safe to run in a child process."""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel

from .schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)

AUDIT_MIN_ROWS = 100


def build_dag_from_mapping(
    treatment: str,
    outcome: str,
    confounders: List[str],
    instrument: Optional[str],
) -> DAGSpec:
    """Canonical backdoor DAG: confounders point at both treatment and outcome,
    treatment points at outcome, and an instrument (if any) points at treatment
    only (the exclusion restriction). DAGSpec rejects self-loops, so a confounder
    equal to the treatment/outcome raises."""
    edges: List[Tuple[str, str]] = []
    for c in confounders:
        edges.append((c, treatment))
        edges.append((c, outcome))
    edges.append((treatment, outcome))
    if instrument:
        edges.append((instrument, treatment))
    return DAGSpec(edges=edges)


class DataQuality(BaseModel):
    n_input: int
    n_clean: int
    n_dropped: int
    treatment_is_binary: bool
    warnings: List[str] = []


def validate_and_prepare(df: pd.DataFrame, mapping: dict) -> Tuple[pd.DataFrame, DataQuality]:
    """Coerce mapped columns to numeric, drop rows with missing values, enforce a
    minimum sample size, and binarise a non-binary treatment at its median.
    Raises ValueError (→ a clear 400 / failed job) on unrecoverable problems."""
    treatment = mapping["treatment"]
    outcome = mapping["outcome"]
    confounders = list(mapping.get("confounders") or [])
    instrument = mapping.get("instrument")
    cols = [treatment, outcome, *confounders] + ([instrument] if instrument else [])

    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"columns not found in data: {missing}")

    warnings: List[str] = []
    work = df[cols].copy()
    for c in cols:
        coerced = pd.to_numeric(work[c], errors="coerce")
        if coerced.isna().sum() > work[c].isna().sum():
            warnings.append(f"column '{c}' had non-numeric values that were dropped")
        work[c] = coerced

    n_input = len(work)
    work = work.dropna()
    n_clean = len(work)
    n_dropped = n_input - n_clean
    if n_clean < AUDIT_MIN_ROWS:
        raise ValueError(
            f"only {n_clean} usable rows after cleaning; need >= {AUDIT_MIN_ROWS}"
        )

    uniq = sorted(work[treatment].unique())
    treatment_is_binary = len(uniq) == 2
    if set(uniq) <= {0.0, 1.0}:
        pass  # already 0/1
    elif treatment_is_binary:
        hi = uniq[1]
        work[treatment] = (work[treatment] == hi).astype(float)
    else:
        median = float(work[treatment].median())
        work[treatment] = (work[treatment] >= median).astype(float)
        warnings.append(
            f"treatment '{treatment}' was continuous; binarised at its median ({median:.3g})"
        )

    dq = DataQuality(
        n_input=n_input, n_clean=n_clean, n_dropped=n_dropped,
        treatment_is_binary=treatment_is_binary, warnings=warnings,
    )
    return work.reset_index(drop=True), dq


def select_methods(instrument: Optional[str]) -> List[str]:
    """Fast, modern, doubly-robust default. IV only when an instrument is mapped.
    The slow classical DoWhy bootstrap methods and forest_dr (broken on binary
    outcomes) are deliberately excluded."""
    methods = ["double_ml", "tmle"]
    if instrument:
        methods.append("iv")
    return methods


def build_query_from_mapping(clean_df: pd.DataFrame, mapping: dict) -> CounterfactualQuery:
    """Build a CounterfactualQuery from a cleaned df + mapping. Treatment is already
    0/1 after validate_and_prepare, so actual=1 / counterfactual=0."""
    treatment = mapping["treatment"]
    outcome = mapping["outcome"]
    confounders = list(mapping.get("confounders") or [])
    instrument = mapping.get("instrument")
    return CounterfactualQuery(
        question=f"Causal effect of '{treatment}' on '{outcome}' (user audit).",
        treatment=InterventionSpec(column=treatment, actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column=outcome, agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=build_dag_from_mapping(treatment, outcome, confounders, instrument),
        dataset=DatasetRef(source_id=f"uploaded_file:{mapping['uploaded_file']}"),
        audience="auditor",
    )


def identification_statement(mapping: dict) -> str:
    conf = ", ".join(mapping.get("confounders") or []) or "(none specified)"
    s = (
        "This estimate is valid only under the assumption of no unmeasured "
        f"confounding beyond the adjusted variables: {conf}. "
    )
    if mapping.get("instrument"):
        s += (
            f"The instrument '{mapping['instrument']}' is additionally assumed to "
            "affect the treatment but the outcome only through the treatment "
            "(the exclusion restriction)."
        )
    else:
        s += (
            "No instrument was supplied, so this is a backdoor-adjustment estimate; "
            "judge its robustness by the sensitivity bound below."
        )
    return s


def sensitivity_headline(artifact: dict) -> str:
    """Plain-English E-value headline from the strongest available estimate."""
    ok = [e for e in artifact.get("estimates", [])
          if e.get("error") is None and isinstance(e.get("sensitivity"), dict)]
    evals = [e["sensitivity"].get("e_value_point") for e in ok
             if e["sensitivity"].get("e_value_point") is not None]
    if not evals:
        return "Sensitivity to unmeasured confounding was not available for this audit."
    e = max(evals)  # most conservative (largest) E-value across methods
    return (
        f"Robustness: an unmeasured confounder would need an E-value of about {e:.2f} "
        "(on the risk-ratio scale, beyond the measured associations) to fully explain "
        "away this effect."
    )
