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
