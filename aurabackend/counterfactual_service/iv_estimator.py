"""Instrumental-variables (2SLS) ATE — pure NumPy, no dowhy/econml.

The instrument is read from the DAG: any node with an edge to the
treatment but no edge to the outcome (exclusion restriction encoded in
the graph). 2SLS gives a consistent ATE when an unmeasured confounder
biases the naive treatment-outcome association — the canonical
fair-lending audit move ("but-for the instrument-driven variation,
what is the causal effect?").
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

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


# Staiger-Stock rule of thumb: a first-stage F below ~10 marks a weak instrument, for which
# 2SLS is biased toward OLS and its analytic CI is unreliable (BUG-215).
WEAK_INSTRUMENT_F = 10.0


class WeakInstrumentError(ValueError):
    """The instrument(s) cannot support a trustworthy 2SLS estimate."""


def first_stage_diagnostics(
    df: pd.DataFrame,
    treatment: str,
    instruments: List[str],
    confounders: List[str],
) -> Dict[str, float]:
    """First-stage strength: the partial F statistic of the EXCLUDED instruments in
    ``T ~ [1, Z, X]`` versus ``T ~ [1, X]``, plus the rank of the first-stage design."""
    n = len(df)
    intercept = np.ones((n, 1))
    Xc = df[confounders].to_numpy(dtype=float) if confounders else np.empty((n, 0))
    Z = df[instruments].to_numpy(dtype=float)
    T = df[treatment].to_numpy(dtype=float)

    full = np.hstack([intercept, Z, Xc])
    restricted = np.hstack([intercept, Xc])

    def rss(X):
        beta = _ols(X, T)
        r = T - X @ beta
        return float(r @ r)

    q = Z.shape[1]
    rank_full = int(np.linalg.matrix_rank(full))
    dof = n - rank_full
    rss_u, rss_r = rss(full), rss(restricted)
    if dof <= 0 or rss_u <= 0:
        f_stat = float("inf") if rss_u <= 0 < dof else 0.0
    else:
        f_stat = max(((rss_r - rss_u) / q) / (rss_u / dof), 0.0)
    return {
        "first_stage_f": float(f_stat),
        "n": n,
        "n_instruments": q,
        "design_rank": rank_full,
        "design_columns": int(full.shape[1]),
    }


def run_iv_2sls(
    df: pd.DataFrame,
    treatment: str,
    outcome: str,
    instruments: List[str],
    confounders: List[str],
    min_first_stage_f: Optional[float] = None,
) -> Tuple[float, float, float]:
    """Return (point, ci_lower, ci_upper) for the IV ATE of treatment on outcome.

    With ``min_first_stage_f`` set, refuse (``WeakInstrumentError``) when the first-stage F of
    the instruments is below it or the first-stage design is rank-deficient. Off by default so
    direct callers keep their behaviour; the engine turns it on (BUG-215)."""
    if not instruments:
        raise ValueError("IV requires at least one instrument")
    if min_first_stage_f is not None:
        diag = first_stage_diagnostics(df, treatment, instruments, confounders)
        if diag["design_rank"] < diag["design_columns"]:
            raise WeakInstrumentError(
                "first-stage design is rank-deficient "
                f"(rank {diag['design_rank']} of {diag['design_columns']} columns): the instrument "
                "is collinear with the confounders or constant"
            )
        if diag["first_stage_f"] < min_first_stage_f:
            raise WeakInstrumentError(
                f"weak instrument: first-stage F = {diag['first_stage_f']:.2f} < {min_first_stage_f:g}; "
                "the 2SLS estimate and its confidence interval are not reliable"
            )
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

    # Analytic SE. BUG-095: sigma2 must come from the STRUCTURAL residual
    # (using the actual treatment T, not the fitted t_hat) -- t_hat strips
    # out exactly the variation the instrument explains, so residuals
    # against S2 (which contains t_hat) are systematically too small
    # whenever the instrument has real explanatory power, anti-conservatively
    # narrowing the CI. The (X_hat'X_hat)^-1 term still uses the projected
    # design S2 -- that part of the standard 2SLS variance formula is
    # correct as written (Wooldridge, Ch. 15).
    S2_actual = np.hstack([intercept, T, Xc])
    resid = Y - S2_actual @ beta2
    dof = max(n - S2.shape[1], 1)
    sigma2 = float(resid @ resid) / dof
    XtX_inv = np.linalg.pinv(S2.T @ S2)
    se = float(np.sqrt(sigma2 * XtX_inv[1, 1]))
    return point, point - 1.96 * se, point + 1.96 * se
