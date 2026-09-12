"""
UASR Canary Causal Estimate — DSR-007b
========================================
Turns ShimRouter's per-batch outcome history (DSR-007a: which version
handled a batch, and its baseline-distance outcome) into a real causal
estimate of the canary version's effect on drift, via the same
DR-Learner code path the counterfactual audit engine uses.

Design (per the 2026-09-12 DSR-007b decision)
-----------------------------------------------
* **Treatment**: 1.0 for batches the canary version handled, 0.0 for
  batches any other (baseline) version handled. Binary, matching
  ``InterventionSpec(actual=1.0, counterfactual=0.0)``.
* **Outcome**: ``OutcomeRecord.outcome`` -- ``DriftDetector
  .baseline_distance()``'s continuous standardized distance, not a 0/1
  flag. Lower is better (closer to the registered baseline).
* **Covariates / DAG**: every key in ``OutcomeRecord.covariates``
  (row_count, columns_count, hour_of_day, has_baseline) is treated as a
  confounder: ``covariate -> treatment`` and ``covariate -> outcome``,
  plus ``treatment -> outcome`` -- the standard backdoor-adjustment DAG
  shape (see ``tests/_synthetic_data.py::synthetic_dag_full`` for the
  same pattern in the counterfactual engine's own test suite).
* **Cadence / feedback**: called once per ``promote_canary`` check,
  advisory only -- attached as a diagnostic field on the returned dict,
  never changes whether promotion actually happens. That keeps this a
  strictly additive signal to compare against the existing
  average-canary-score rule before anything is allowed to depend on it.

This function must NEVER raise or block a promotion decision on
failure -- causal estimation is best-effort observability, not a gate.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .shim_router import OutcomeRecord

logger = logging.getLogger("uasr.canary_causal_estimator")

# Below this many observations in EACH arm (canary, baseline), don't even
# attempt an estimate -- DR-Learner's cross-fitting needs enough rows per
# fold to be meaningful, and a noisy point estimate from a handful of
# batches is worse than honestly reporting "not enough data yet."
MIN_SAMPLES_PER_ARM = 5


async def estimate_canary_effect(
    history: List[OutcomeRecord],
    canary_version: str,
) -> Optional[Dict[str, Any]]:
    """Estimate the canary version's causal effect on drift distance
    versus every other version in ``history``, using DR-Learner.

    Returns None when there isn't enough history yet (fewer than
    ``MIN_SAMPLES_PER_ARM`` observations in either arm) or when the
    estimate itself fails for any reason -- always fails open rather
    than raising, since this is an advisory signal attached to
    ``promote_canary``'s response, never a gate on promotion.
    """
    treated = [r for r in history if r.version == canary_version]
    control = [r for r in history if r.version != canary_version]
    if len(treated) < MIN_SAMPLES_PER_ARM or len(control) < MIN_SAMPLES_PER_ARM:
        return None

    try:
        import pandas as pd

        from counterfactual_service.engine import run_estimators
        from counterfactual_service.schemas import DAGSpec, InterventionSpec, OutcomeSpec

        rows: List[Dict[str, float]] = []
        covariate_keys: set = set()
        for r in treated + control:
            covariate_keys.update(r.covariates.keys())

        for r in treated + control:
            row: Dict[str, float] = {
                "treatment": 1.0 if r.version == canary_version else 0.0,
                "outcome": float(r.outcome),
            }
            for key in covariate_keys:
                row[key] = float(r.covariates.get(key, 0.0))
            rows.append(row)
        df = pd.DataFrame(rows)

        edges = [[key, "treatment"] for key in covariate_keys]
        edges += [[key, "outcome"] for key in covariate_keys]
        edges.append(["treatment", "outcome"])

        estimates = await run_estimators(
            df=df,
            treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="outcome", agg="mean", window=("1970-01-01", "2099-12-31")),
            dag=DAGSpec(edges=[tuple(e) for e in edges]).model_dump(),
            methods=["double_ml"],
        )
        est = estimates[0] if estimates else None
        if est is None or est.error is not None:
            return {
                "n_treated": len(treated), "n_control": len(control),
                "error": est.error if est is not None else "no estimate returned",
            }
        return {
            "point": est.point,
            "ci_lower": est.ci_lower,
            "ci_upper": est.ci_upper,
            "degraded": est.degraded,
            "n_treated": len(treated),
            "n_control": len(control),
            "interpretation": (
                "negative point = canary reduces drift distance vs baseline "
                "(canary is better); positive = canary increases it"
            ),
        }
    except Exception as exc:  # noqa: BLE001 -- advisory signal, must never raise
        logger.warning(
            "canary causal estimate failed for version=%s: %s", canary_version, exc,
        )
        return {"n_treated": len(treated), "n_control": len(control), "error": str(exc)}
