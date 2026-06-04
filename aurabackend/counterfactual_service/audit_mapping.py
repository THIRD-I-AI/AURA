"""Turn a user column-mapping into a validated DataFrame + a causally-honest
CounterfactualQuery. Pure functions — no I/O, no engine calls — so they're
trivially testable and safe to run in a child process."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
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


CARD_CAP = 12


def _binarise_categorical(work: pd.DataFrame, col: str, role: str, warnings: List[str]) -> None:
    """Encode a 2-value treatment/outcome/instrument to 0/1 in place. Numeric 0/1
    is left as-is; a continuous numeric column is left for the median-binariser;
    a non-numeric column with >2 categories raises (the contrast must be binary)."""
    uniq = sorted(work[col].dropna().unique().tolist())
    numeric = pd.api.types.is_numeric_dtype(work[col])
    if numeric and set(uniq) <= {0, 1}:
        return
    if len(uniq) == 2:
        lo, hi = uniq[0], uniq[1]
        work[col] = work[col].where(work[col].isna(), (work[col] == hi).astype(float))
        warnings.append(f"{role} '{col}' encoded: {lo}=0, {hi}=1")
    elif numeric:
        return  # continuous → existing median-binarisation handles it downstream
    else:
        raise ValueError(
            f"{role} '{col}' has {len(uniq)} categories; the audit needs a binary "
            "contrast — filter to two groups or pick a reference."
        )


def encode_for_audit(
    df: pd.DataFrame, mapping: dict, card_cap: int = CARD_CAP
) -> Tuple[pd.DataFrame, dict, List[str]]:
    """Auto-encode categorical columns so a raw real-world CSV can be audited
    without manual numeric encoding. Returns (encoded_df, effective_mapping,
    warnings). The effective mapping carries the encoded confounder column names
    (one-hot dummies) so the DAG adjusts on them.

    Raises ValueError on a >2-category treatment/outcome/instrument or a
    high-cardinality categorical confounder (> ``card_cap``)."""
    work = df.copy()
    eff = dict(mapping)
    warnings: List[str] = []

    treatment = mapping["treatment"]
    outcome = mapping["outcome"]
    confounders = list(mapping.get("confounders") or [])
    instrument = mapping.get("instrument")

    _binarise_categorical(work, treatment, "treatment", warnings)

    if not pd.api.types.is_numeric_dtype(work[outcome]):
        uniq = sorted(work[outcome].dropna().unique().tolist())
        if len(uniq) == 2:
            work[outcome] = work[outcome].where(
                work[outcome].isna(), (work[outcome] == uniq[1]).astype(float)
            )
            warnings.append(f"outcome '{outcome}' encoded: {uniq[0]}=0, {uniq[1]}=1")
        else:
            raise ValueError(
                f"outcome '{outcome}' must be numeric or binary (has {len(uniq)} categories)."
            )

    if instrument:
        _binarise_categorical(work, instrument, "instrument", warnings)

    new_confounders: List[str] = []
    for c in confounders:
        if pd.api.types.is_numeric_dtype(work[c]):
            new_confounders.append(c)
            continue
        distinct = work[c].dropna().nunique()
        if distinct > card_cap:
            raise ValueError(
                f"confounder '{c}' has {distinct} categories (> {card_cap}); "
                "drop or bucket it before auditing."
            )
        mask_na = work[c].isna()
        dummies = pd.get_dummies(work[c], prefix=c, drop_first=True).astype(float)
        # Missing categoricals must propagate to NaN so the downstream dropna
        # removes those rows (rather than silently treating NaN as the reference).
        dummies.loc[mask_na, :] = np.nan
        work = work.drop(columns=[c])
        for dcol in dummies.columns:
            work[dcol] = dummies[dcol]
        new_confounders.extend(list(dummies.columns))
        warnings.append(
            f"confounder '{c}' one-hot encoded → {', '.join(dummies.columns)} (drop-first)"
        )
    eff["confounders"] = new_confounders

    return work, eff, warnings


def validate_and_prepare(
    df: pd.DataFrame, mapping: dict
) -> Tuple[pd.DataFrame, DataQuality, dict]:
    """Auto-encode categorical columns (see ``encode_for_audit``), coerce mapped
    columns to numeric, drop rows with missing values, enforce a minimum sample
    size, and binarise a non-binary treatment at its median. Returns
    ``(clean_df, DataQuality, effective_mapping)`` — the effective mapping carries
    the encoded column names so the DAG adjusts on them. Raises ValueError (→ a
    clear 400 / failed job) on unrecoverable problems."""
    # Validate the user's mapped columns exist before we touch the frame.
    orig_cols = [mapping["treatment"], mapping["outcome"], *(mapping.get("confounders") or [])]
    if mapping.get("instrument"):
        orig_cols.append(mapping["instrument"])
    missing = [c for c in orig_cols if c not in df.columns]
    if missing:
        raise ValueError(f"columns not found in data: {missing}")

    encoded, eff, warnings = encode_for_audit(df, mapping)

    treatment = eff["treatment"]
    outcome = eff["outcome"]
    confounders = list(eff.get("confounders") or [])
    instrument = eff.get("instrument")
    cols = [treatment, outcome, *confounders] + ([instrument] if instrument else [])

    work = encoded[cols].copy()
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
    return work.reset_index(drop=True), dq, eff


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
