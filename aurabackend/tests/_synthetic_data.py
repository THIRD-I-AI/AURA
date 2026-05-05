"""Synthetic data helpers for counterfactual engine tests.

Default DGP — binary treatment, the common counterfactual case:

    seasonality ~ N(0, 1)
    propensity  = sigmoid(0.6 * seasonality)
    treatment   = Bernoulli(propensity)              # binary 0/1
    outcome     = TRUE_EFFECT * treatment + 1.0 * seasonality + N(0, 1)

The *unconfounded* effect of treatment on outcome is ``TRUE_EFFECT``.
Linear regression, IPW, PSM and double-ML *all* apply to binary
treatments, so the fan-out test is a fair check on the engine's
contract that every requested method either succeeds or returns a
structured error.

A continuous-treatment variant is provided for tests that exercise the
"some methods don't apply" path (PSM/IPW will return errors there).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRUE_EFFECT: float = 1.5


def synthetic_dataset(n: int = 800, seed: int = 0xfeed_dead) -> pd.DataFrame:
    """Binary-treatment synthetic dataset (default for engine tests)."""
    rng = np.random.default_rng(seed)
    seasonality = rng.standard_normal(n)
    propensity = 1.0 / (1.0 + np.exp(-0.6 * seasonality))
    treatment = (rng.uniform(size=n) < propensity).astype(int)
    outcome = TRUE_EFFECT * treatment + 1.0 * seasonality + rng.standard_normal(n)
    return pd.DataFrame({
        "seasonality": seasonality,
        "treatment": treatment,
        "outcome": outcome,
    })


def synthetic_dataset_continuous(n: int = 800, seed: int = 0xfeed_dead) -> pd.DataFrame:
    """Continuous-treatment variant — only LR + double-ML apply.

    Used by the eval-gate adversarial-detection layer where we want
    *some* estimators to refuse and the critic to still flag the DAG.
    """
    rng = np.random.default_rng(seed)
    seasonality = rng.standard_normal(n)
    treatment = 0.5 * seasonality + rng.standard_normal(n)
    outcome = TRUE_EFFECT * treatment + 1.0 * seasonality + rng.standard_normal(n)
    return pd.DataFrame({
        "seasonality": seasonality,
        "treatment": treatment,
        "outcome": outcome,
    })


def synthetic_dag_full() -> dict:
    """Correct DAG: includes seasonality as a confounder of treatment+outcome."""
    return {"edges": [
        ["seasonality", "outcome"],
        ["seasonality", "treatment"],
        ["treatment", "outcome"],
    ]}


def synthetic_dag_missing_confounder() -> dict:
    """Broken DAG: omits seasonality. Estimators will overestimate;
    an honest critic should flag it."""
    return {"edges": [["treatment", "outcome"]]}
