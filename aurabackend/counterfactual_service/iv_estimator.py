"""Instrumental-variables (2SLS) ATE — pure NumPy, no dowhy/econml.

The instrument is read from the DAG: any node with an edge to the
treatment but no edge to the outcome (exclusion restriction encoded in
the graph). 2SLS gives a consistent ATE when an unmeasured confounder
biases the naive treatment-outcome association — the canonical
fair-lending audit move ("but-for the instrument-driven variation,
what is the causal effect?").
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd


def instruments_from_dag(edges, treatment: str, outcome: str) -> List[str]:
    to_treatment = {src for src, dst in edges if dst == treatment}
    to_outcome = {src for src, dst in edges if dst == outcome}
    insts = [n for n in to_treatment if n != outcome and n not in to_outcome]
    return sorted(insts)


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def run_iv_2sls(
    df: pd.DataFrame,
    treatment: str,
    outcome: str,
    instruments: List[str],
    confounders: List[str],
) -> Tuple[float, float, float]:
    """Return (point, ci_lower, ci_upper) for the IV ATE of treatment on outcome."""
    if not instruments:
        raise ValueError("IV requires at least one instrument")
    n = len(df)
    intercept = np.ones((n, 1))
    Xc = df[confounders].to_numpy(dtype=float) if confounders else np.empty((n, 0))
    Z = df[instruments].to_numpy(dtype=float)
    T = df[treatment].to_numpy(dtype=float).reshape(-1, 1)
    Y = df[outcome].to_numpy(dtype=float)

    # Stage 1: T ~ [intercept, instruments, confounders]
    S1 = np.hstack([intercept, Z, Xc])
    t_hat = S1 @ _ols(S1, T.ravel())

    # Stage 2: Y ~ [intercept, t_hat, confounders]
    S2 = np.hstack([intercept, t_hat.reshape(-1, 1), Xc])
    beta2 = _ols(S2, Y)
    point = float(beta2[1])  # coefficient on fitted treatment

    # Analytic SE from stage-2 residuals.
    resid = Y - S2 @ beta2
    dof = max(n - S2.shape[1], 1)
    sigma2 = float(resid @ resid) / dof
    XtX_inv = np.linalg.pinv(S2.T @ S2)
    se = float(np.sqrt(sigma2 * XtX_inv[1, 1]))
    return point, point - 1.96 * se, point + 1.96 * se
