"""
Counterfactual Audit Engine — orchestration layer.

Estimator + refuter fan-out lives here for cohesion: they share the same
treatment/outcome/data inputs and the engine is the only consumer.

Contract surface (the rest of the service depends on these names):

* ``score_confidence(estimates, refutations, challenges) -> "low|medium|high"``
* ``run_estimators(df, treatment, outcome, dag) -> List[CounterfactualEstimate]``
* ``run_refuters(df, treatment, outcome, dag)   -> List[RefutationResult]``
* ``run_job(query, df) -> CounterfactualArtifact``
* ``dowhy_available() -> bool``
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from itertools import combinations
from typing import Any, Dict, List, Literal, Optional

import pandas as pd

from . import critic_cache, persistence, signing
from .canonical import canonical_dumps, sha256_canonical
from .schemas import (
    AdversarialChallenge,
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
    EstimatorMethod,
    InterventionSpec,
    OutcomeSpec,
    PropensityDiagnostics,
    RefutationResult,
    RefuterName,
    SensitivityReport,
    Severity,
)
from .sensitivity import compute_sensitivity_report

logger = logging.getLogger("aura.counterfactual.engine")


# ── Optional dep ──────────────────────────────────────────────────────

try:
    from dowhy import CausalModel  # type: ignore
    _DOWHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    CausalModel = None  # type: ignore[assignment]
    _DOWHY_AVAILABLE = False


def dowhy_available() -> bool:
    return _DOWHY_AVAILABLE


# Sprint 12 — EconML LinearDRLearner replaces the prior double_ml stub
# (DoWhy backdoor.linear_regression with all confounders). DR is a doubly
# robust estimator: as long as either the propensity model OR the outcome
# model is correctly specified, the ATE estimate is consistent. Cross-
# fitted nuisance models keep the asymptotic guarantees while shrinking
# the finite-sample bias the linear-regression stub had on small n.
try:
    from econml.dr import ForestDRLearner, LinearDRLearner  # type: ignore
    _ECONML_AVAILABLE = True
except ImportError:  # pragma: no cover
    LinearDRLearner = None  # type: ignore[assignment]
    ForestDRLearner = None  # type: ignore[assignment]
    _ECONML_AVAILABLE = False


def econml_available() -> bool:
    return _ECONML_AVAILABLE


# ── Confidence scoring (deterministic, no LLM) ────────────────────────

def _ci_pair_overlap(a: CounterfactualEstimate, b: CounterfactualEstimate) -> bool:
    return not (a.ci_upper < b.ci_lower or b.ci_upper < a.ci_lower)


def pairwise_ci_overlap_rate(estimates: List[CounterfactualEstimate]) -> float:
    """Fraction of estimator pairs whose 95% CIs overlap.

    With <2 valid estimates the rate is undefined; we return 1.0 for a
    single valid estimate (no disagreement to penalise) and 0.0 for none
    (no information).
    """
    valid = [e for e in estimates if e.error is None]
    if len(valid) < 2:
        return 1.0 if valid else 0.0
    pairs = list(combinations(valid, 2))
    overlaps = sum(_ci_pair_overlap(a, b) for a, b in pairs)
    return overlaps / len(pairs)


def score_confidence(
    estimates: List[CounterfactualEstimate],
    refutations: List[RefutationResult],
    challenges: List[AdversarialChallenge],
) -> Severity:
    """Pure deterministic confidence: 0.5*refute_pass + 0.4*ci_overlap - 0.3*high_sev."""
    refute_pass = (
        sum(r.passed for r in refutations) / len(refutations) if refutations else 0.0
    )
    ci_overlap = pairwise_ci_overlap_rate(estimates)
    high_sev = sum(1 for c in challenges if c.severity == "high")
    raw = 0.5 * refute_pass + 0.4 * ci_overlap - 0.3 * high_sev
    if raw > 0.7:
        return "high"
    if raw > 0.4:
        return "medium"
    return "low"


# ── DoWhy method registries ───────────────────────────────────────────

_DOWHY_ESTIMATOR_METHODS: Dict[EstimatorMethod, str] = {
    "linear_regression": "backdoor.linear_regression",
    "ipw": "backdoor.propensity_score_weighting",
    "psm": "backdoor.propensity_score_matching",
    # Double-ML stub: backdoor.linear_regression with all confounders
    # forced into the adjustment set. Real EconML DRLearner lands in S11+.
    "double_ml": "backdoor.linear_regression",
}

_DOWHY_REFUTER_METHODS: Dict[RefuterName, str] = {
    "random_common_cause": "random_common_cause",
    "placebo": "placebo_treatment_refuter",
    "data_subset": "data_subset_refuter",
    "sensitivity": "add_unobserved_common_cause",
}


# ── Sprint 16: split-conformal calibration for DR estimators ─────────

def _conformal_ate_via_aipw(
    X: Any,
    W: Optional[Any],
    T_bin: Any,
    Y: Any,
    cv_folds: int,
    seed: int,
    alpha: float = 0.05,
) -> Optional[Dict[str, float]]:
    """Split-conformal ATE inference via AIPW pseudo-outcomes.

    Sprint 16. Where the asymptotic CI (statsmodels sandwich on
    LinearDR, BLB on ForestDR) needs the nuisance models to be
    correctly specified for its coverage guarantee, the conformal
    interval below holds at the stated ``1-alpha`` level in finite
    samples REGARDLESS of nuisance-model quality — the only
    requirement is the calibration set being iid with the deployment
    population.

    Procedure (Lei & Candès JRSS-B 2021, Algorithm 1, adapted for ATE):

      1. Split data 70/30 into proper-train / calibration using a
         seed derived from the engine's per-method seed so the split
         is byte-stable across re-runs.
      2. Fit calibrated logistic propensity + linear outcome nuisance
         models on the proper-train fold only. Same model classes
         the LinearDR path uses so the calibration set sees the
         same kind of nuisance regression.
      3. Predict e_hat, mu0_hat, mu1_hat on the calibration rows.
      4. Compute AIPW pseudo-outcomes (counterfactual_service.
         conformal.aipw_pseudo_outcomes).
      5. ``pt = mean(psi)``; half-width via weighted_split_conformal
         on |psi - pt|.

    Returns a dict ``{point, ci_lower, ci_upper}`` rounded to 6
    decimals (Layer 10 byte stability) when the calibration set is
    large enough to certify coverage at alpha; returns ``None``
    otherwise — in which case the caller keeps the asymptotic CI
    rather than ship a fake-tight conformal interval.

    Defensive on small n: when the calibration fold has < 30 rows
    or per-class count < 5, return None. With only a handful of
    calibration rows the conformal quantile is so wide as to be
    uninformative — better to surface the asymptotic CI explicitly
    than to ship a |Y_max - Y_min|-sized "conformal" band that
    technically satisfies coverage but tells the operator nothing.
    """
    try:
        import numpy as np
        from sklearn.linear_model import LinearRegression, LogisticRegression

        from .conformal import aipw_pseudo_outcomes, weighted_split_conformal

        X_arr = np.asarray(X, dtype=float)
        Y_arr = np.asarray(Y, dtype=float).ravel()
        T_arr = np.asarray(T_bin, dtype=int).ravel()
        n = X_arr.shape[0]
        if n < 50:
            # Not enough rows to do a 70/30 split AND have a
            # meaningful calibration set. The asymptotic CI is the
            # honest fallback.
            return None

        # Seed-derived split — _seed_for already in scope (Sprint 11
        # primitive). Reuse so two engine runs on the same input
        # produce the same proper-train / calibration partition.
        split_rng = np.random.default_rng(seed ^ 0x5151_C0E0)
        order = split_rng.permutation(n)
        n_train = int(0.70 * n)
        train_idx = order[:n_train]
        calib_idx = order[n_train:]
        if len(calib_idx) < 30:
            return None

        # Per-class minimum check: logistic propensity needs each
        # class represented in BOTH folds for sklearn to fit without
        # raising. A degenerate split where one fold is all-T=1 or
        # all-T=0 is too noisy for conformal anyway.
        for idx in (train_idx, calib_idx):
            t_sub = T_arr[idx]
            if int(t_sub.sum()) < 5 or int((1 - t_sub).sum()) < 5:
                return None

        # Build the nuisance-model feature matrix: include W
        # alongside X if W is provided as a separate object. The DR
        # paths pass X = W in the with-DAG branch so this concatenation
        # is a no-op there; in the broken-DAG branch (W=None, X=noise)
        # it keeps the propensity model fit-able on the noise column
        # alone — produces a near-constant e_hat ~ 0.5 which the
        # AIPW correction handles gracefully.
        if W is None or (hasattr(W, "shape") and W.shape == X_arr.shape and (W is X_arr or np.allclose(W, X_arr))):
            features = X_arr
        else:
            features = np.hstack([X_arr, np.asarray(W, dtype=float)])

        # Fit nuisance on train fold only — this is the key
        # difference from S12's cross-fitted LinearDR. Split-
        # conformal needs the calibration set unseen during nuisance
        # fit for the (1-alpha) quantile guarantee.
        propensity_model = LogisticRegression(
            solver="lbfgs", max_iter=200, random_state=seed,
        )
        propensity_model.fit(features[train_idx], T_arr[train_idx])
        e_hat_calib = propensity_model.predict_proba(features[calib_idx])[:, 1]

        # Two outcome models — one per arm, fit on the corresponding
        # subset of the training fold. Matches the standard AIPW
        # decomposition.
        train_T_mask = T_arr[train_idx] == 1
        train_C_mask = T_arr[train_idx] == 0
        if int(train_T_mask.sum()) < 5 or int(train_C_mask.sum()) < 5:
            return None
        mu1_model = LinearRegression()
        mu0_model = LinearRegression()
        mu1_model.fit(features[train_idx][train_T_mask], Y_arr[train_idx][train_T_mask])
        mu0_model.fit(features[train_idx][train_C_mask], Y_arr[train_idx][train_C_mask])
        mu1_hat_calib = mu1_model.predict(features[calib_idx])
        mu0_hat_calib = mu0_model.predict(features[calib_idx])

        psi = aipw_pseudo_outcomes(
            Y=Y_arr[calib_idx],
            T=T_arr[calib_idx],
            e_hat=e_hat_calib,
            mu0_hat=mu0_hat_calib,
            mu1_hat=mu1_hat_calib,
        )
        pt = float(np.mean(psi))
        scores = np.abs(psi - pt)
        half_width = weighted_split_conformal(scores, weights=None, alpha=alpha)
        if not np.isfinite(half_width):
            # Sample too small to certify coverage at alpha — caller
            # should keep the asymptotic CI.
            return None

        return {
            "point": round(pt, 6),
            "ci_lower": round(pt - half_width, 6),
            "ci_upper": round(pt + half_width, 6),
        }
    except Exception as exc:
        logger.warning("Conformal calibration failed (non-fatal, asymptotic CI retained): %s", exc)
        return None


# ── Sprint 12: EconML DR-Learner direct path ─────────────────────────

def _compute_propensity_diagnostics(
    *,
    X: Any,
    W: Optional[Any],
    T_bin: Any,
    cv_folds: int,
    seed: int,
) -> Optional[PropensityDiagnostics]:
    """Cross-fit a propensity model and return quantile diagnostics.

    Mirrors the propensity nuisance DR-Learner runs internally — same
    LogisticRegression class, same per-fold seeding, same StratifiedKFold
    splitter — but returns the out-of-fold predictions directly so we
    can summarise the distribution. EconML doesn't expose the cross-
    fitted propensities through a stable attribute (the internal name
    has shifted between releases), so re-cross-fitting is the most
    durable way to capture them.

    Returns None on any failure — propensity diagnostics are
    *advisory*, not load-bearing, and the artifact must still seal
    even if this side-channel cross-fit fails.
    """
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict

        # Feature matrix mirrors the propensity nuisance input: X and W
        # concatenated. When W is None or aliased to X (the with-DAG
        # branch), pass X alone to avoid duplicating columns.
        prop_features = (
            np.hstack([X, W]) if W is not None and W is not X else X
        )
        splitter = StratifiedKFold(
            n_splits=cv_folds, shuffle=True, random_state=seed,
        )
        proba = cross_val_predict(
            LogisticRegression(
                solver="lbfgs", max_iter=200, random_state=seed,
            ),
            X=prop_features,
            y=T_bin,
            cv=splitter,
            method="predict_proba",
        )
        # Column 1 = P(T=1); cross_val_predict guarantees rows in
        # original order so quantile stats are over the training set.
        e = proba[:, 1]
        q = np.quantile(e, [0.05, 0.25, 0.50, 0.75, 0.95])
        return PropensityDiagnostics(
            quantiles={
                "p05": float(q[0]),
                "p25": float(q[1]),
                "p50": float(q[2]),
                "p75": float(q[3]),
                "p95": float(q[4]),
            },
            min=float(e.min()),
            max=float(e.max()),
            mean=float(e.mean()),
            # An "extreme" propensity is one in the IPW-fragile region
            # below 0.05 or above 0.95. The threshold is the same one
            # used in [[feedback_propensity_calibration]] for catching
            # uncalibrated propensity models, kept consistent here so
            # the auditor diagnostic and the development guideline
            # speak the same number.
            n_extreme=int(((e < 0.05) | (e > 0.95)).sum()),
            n_total=int(len(e)),
        )
    except Exception as exc:
        logger.warning("Propensity diagnostics capture failed: %s", exc)
        return None


def _run_one_econml_dr_learner(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    seed: int,
    conformal_calibration: bool = False,
) -> CounterfactualEstimate:
    """LinearDRLearner ATE for the ``double_ml`` slot.

    Direct EconML call (no DoWhy bridge) — gives us control over the
    cross-fit splitter, nuisance learners, and inference type. Routed
    only when ``_ECONML_AVAILABLE`` is True; otherwise ``_run_one_
    estimator`` falls back to the DoWhy stub.

    Determinism: ``random_state=seed`` is threaded into LinearDRLearner
    (controls the cross-fit KFold), RandomForestClassifier (propensity
    nuisance), and RandomForestRegressor (outcome nuisance). ``_seed_
    numpy(seed)`` is called first so any internal np.random use is also
    pinned. Default ``n_jobs=None`` keeps sklearn single-threaded —
    parallelism would interleave RNG and break Layer 10 byte-identity.

    The dispatcher's ``X=zeros(n, 1)`` is intentional: AURA returns ATE
    (a single number), not a CATE vector, so we feed a constant feature
    and read out one effect. Confounders go entirely through ``W``.
    """
    t0 = time.perf_counter()
    try:
        # Heavy sklearn import is local to keep the engine importable
        # without econml/sklearn — the dispatcher already gates on
        # _ECONML_AVAILABLE before we get here.
        #
        # Nuisance model choice: LogisticRegression for propensity gives
        # well-calibrated probabilities (Platt-style), avoiding the IPW
        # blow-up that RandomForestClassifier can produce when its
        # predict_proba lands near 0 or 1. LinearRegression for the
        # outcome stage matches the linear DGP the eval-gate uses and
        # gives the estimator a fighting chance on small n.
        # RandomForest-based nuisances would be more flexible for non-
        # linear DGPs but are unstable on the eval-gate's n=1000 with
        # only one or two control columns.
        import numpy as np
        from sklearn.linear_model import LinearRegression, LogisticRegression

        _seed_numpy(seed)

        # T must be binary 0/1 (DRLearner requires discrete treatment).
        # Binarise by equality with the InterventionSpec's "actual"
        # value: row gets 1 if it matches the factual treatment, 0
        # otherwise. The counterfactual contrast is "T=actual vs
        # T=counterfactual" and the engine convention is to point the
        # estimator at the 1 vs 0 case.
        T_bin = (df[treatment.column] == treatment.actual).astype(int).to_numpy()
        if T_bin.sum() == 0 or T_bin.sum() == len(T_bin):
            # Degenerate: no variation in treatment → DR cannot estimate.
            raise ValueError(
                "Treatment column has no variation under the binarisation "
                f"T = (col == {treatment.actual}); cannot fit DR-Learner"
            )
        Y = df[outcome.column].to_numpy(dtype=float)

        # DAG-aware backdoor adjustment: take the parents of the
        # treatment node in the supplied DAG as the control set. This
        # mirrors what DoWhy's backdoor identification does on the same
        # edges and — critically — preserves the Layer 11 contract: a
        # DAG that omits the seasonality→treatment edge yields an empty
        # control set, so the estimator overstates the effect and the
        # critic gets to flag the missing confounder.
        treatment_parents = sorted({
            src for src, dst in dag.get("edges", [])
            if dst == treatment.column
            and src != outcome.column   # outcome can't be a confounder
            and src in df.columns       # must be present in the data
        })

        n = len(df)
        # EconML's LinearDRLearner requires a non-degenerate X (the
        # heterogeneity features that drive the linear final stage). Two
        # cases:
        #   1. We have DAG-identified confounders → pass them as both X
        #      AND W. The propensity + outcome nuisance models then see
        #      them as confounders, and the final linear stage uses them
        #      as heterogeneity drivers. ATE comes out as mean(effect).
        #   2. No DAG-identified confounders (broken DAG / Layer 11) →
        #      pass a deterministic seed-derived noise column for X so
        #      the linear final stage isn't underdetermined, with W=None
        #      so no confounder adjustment happens. The estimator will
        #      overstate the effect (correctly demonstrating the missing
        #      confounder), and the critic will flag it.
        if treatment_parents:
            X = df[treatment_parents].to_numpy(dtype=float)
            W = X
        else:
            # Deterministic noise column so the linear final stage is
            # solvable; uses the same seed as the rest of the path so
            # Layer 10 byte-identity holds.
            X = np.random.default_rng(seed).standard_normal((n, 1))
            W = None

        # KFold needs >= cv samples per fold per treatment level. With
        # binary T and small n the safe ceiling is min(3, min(class_count)//2).
        per_class = min(int(T_bin.sum()), int((1 - T_bin).sum()))
        cv_folds = max(2, min(3, per_class // 2)) if per_class >= 4 else 2

        est = LinearDRLearner(
            model_propensity=LogisticRegression(
                solver="lbfgs", max_iter=200, random_state=seed,
            ),
            model_regression=LinearRegression(),
            cv=cv_folds,
            random_state=seed,
        )
        est.fit(Y=Y, T=T_bin, X=X, W=W, inference="statsmodels")

        # ATE = mean of per-row treatment effects across the fitting set.
        # This is the canonical ATE estimate for a heterogeneous-effects
        # model: we're not asking "what's the effect at X=0", we're
        # asking "what's the average effect across the population we
        # observed". The per-row CIs from effect_interval are averaged
        # to give a scalar interval — conservative (it's the average of
        # CIs, not the CI of the average) but consistent and stable.
        effects = est.effect(X, T0=0, T1=1)
        pt = float(np.mean(effects))
        lo_arr, hi_arr = est.effect_interval(X, T0=0, T1=1, alpha=0.05)
        ci_lower = float(np.mean(lo_arr))
        ci_upper = float(np.mean(hi_arr))

        # Sprint 13: capture cross-fitted propensity diagnostics so the
        # auditor can see the distribution of e(X) the estimator used.
        # We refit the propensity model under the same splitter + same
        # random_state DR-Learner uses internally — equivalent to
        # reading DR's own fold-wise propensity predictions but more
        # robust to EconML's internal attribute naming, which has
        # shifted between releases. The overhead is one extra logistic
        # regression cross-fit (sub-100ms on n=1000) and the result
        # round-trips through the artifact hash so propensity drift
        # surfaces as a hash change to anyone replaying the audit.
        propensity_diag = _compute_propensity_diagnostics(
            X=X, W=W, T_bin=T_bin, cv_folds=cv_folds, seed=seed,
        )
        # Surface a NaN result as a structured failure rather than
        # letting it propagate into the artifact. NaN can come from an
        # underdetermined final-stage covariance or from extreme
        # propensities the LogisticRegression couldn't pin away from
        # the [0, 1] boundary on degenerate data. Raising here lets the
        # outer except path build a CounterfactualEstimate with
        # ``error=...`` so the operator UI and the audit chain both see
        # the failure explicitly.
        if not (np.isfinite(pt) and np.isfinite(ci_lower) and np.isfinite(ci_upper)):
            raise ValueError(
                f"DR-Learner produced non-finite output "
                f"(point={pt}, ci=[{ci_lower}, {ci_upper}]); "
                "likely an underdetermined final stage or degenerate propensities"
            )
        if ci_upper < ci_lower:
            ci_lower, ci_upper = ci_upper, ci_lower

        # Sprint 16: optional split-conformal calibration. When the
        # caller opts in (typically via methods + conformal_
        # calibration=True from run_estimators) AND the calibration
        # set is large enough to certify coverage at alpha=0.05, we
        # overwrite the asymptotic [ci_lower, ci_upper] with the
        # conformal interval and flip ci_method to "conformal". The
        # asymptotic CI is kept when conformal calibration declines
        # to certify (small n, degenerate split) — the operator card
        # sees ci_method="asymptotic" in that case which is the
        # honest signal.
        ci_method: str = "asymptotic"
        if conformal_calibration:
            conformal_result = _conformal_ate_via_aipw(
                X=X, W=W, T_bin=T_bin, Y=Y,
                cv_folds=cv_folds, seed=seed,
            )
            if conformal_result is not None:
                pt = conformal_result["point"]
                ci_lower = conformal_result["ci_lower"]
                ci_upper = conformal_result["ci_upper"]
                ci_method = "conformal"

        return CounterfactualEstimate(
            method="double_ml",
            point=pt,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            n_samples=n,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            propensity_diagnostics=propensity_diag,
            ci_method=ci_method,  # type: ignore[arg-type]
        )
    except Exception as exc:
        logger.warning("DR-Learner (econml) failed: %s", exc)
        return CounterfactualEstimate(
            method="double_ml",
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


# ── Sprint 15: EconML ForestDRLearner — non-parametric CATE ──────────

def _run_one_econml_forest_dr_learner(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    seed: int,
    conformal_calibration: bool = False,
) -> CounterfactualEstimate:
    """ForestDRLearner ATE + per-row CATE distribution.

    Sprint 15. Where ``LinearDRLearner`` (the ``double_ml`` slot) fits
    a linear final stage and assumes the CATE function is well-
    approximated by a hyperplane in X, ``ForestDRLearner`` swaps that
    final stage for a Subsampled Honest Forest regressor — a Wager &
    Athey (2018) / GRF (Athey, Tibshirani & Wager 2019)-style estimator
    that produces a non-parametric CATE function and a per-row CATE
    vector. We capture that vector as 10 quantiles in
    ``cate_distribution`` (rounded to 6 decimals so the canonical-JSON
    bytes are stable across re-runs).

    Determinism: same seed pattern as the LinearDR path. The forest's
    bootstrap-of-little-bags inference samples a lot of np.random
    state; ``_seed_numpy(seed)`` + ``random_state=seed`` everywhere
    keeps it pinned. ``n_jobs=1`` (the default) keeps sklearn single-
    threaded — parallelism would interleave the BLB iterations and
    break Layer 10 byte-identity. ``n_estimators=50`` (default is 100)
    keeps total runtime reasonable on the eval-gate n=300..600 range.

    Propensity calibration: ``CalibratedClassifierCV`` wrapping a
    ``GradientBoostingClassifier`` — non-linear nuisance is the whole
    point of Forest paths, but uncalibrated boosted-tree probabilities
    land near 0/1 the same way RF does (see
    [[feedback_propensity_calibration]]). Isotonic calibration via CV
    keeps the IPW correction stable. ``cv=3`` on both the calibrator
    and the DR's cross-fit keeps the same fold count throughout.

    Routed only when ``_ECONML_AVAILABLE``; called only when the caller
    explicitly opts in via ``methods=["forest_dr", ...]``. Opt-in
    rather than default-fan-out because (a) it adds ~3-5 seconds per
    job, (b) older Layer 9/Layer 11 eval-gate assertions count exactly
    four estimators, and (c) the linear DR path is the right default
    when the DGP is linear-in-X — only switch when the operator wants
    heterogeneity visibility.
    """
    t0 = time.perf_counter()
    try:
        import numpy as np
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor

        _seed_numpy(seed)

        T_bin = (df[treatment.column] == treatment.actual).astype(int).to_numpy()
        if T_bin.sum() == 0 or T_bin.sum() == len(T_bin):
            raise ValueError(
                "Treatment column has no variation under the binarisation "
                f"T = (col == {treatment.actual}); cannot fit ForestDR-Learner"
            )
        Y = df[outcome.column].to_numpy(dtype=float)

        treatment_parents = sorted({
            src for src, dst in dag.get("edges", [])
            if dst == treatment.column
            and src != outcome.column
            and src in df.columns
        })

        n = len(df)
        if treatment_parents:
            X = df[treatment_parents].to_numpy(dtype=float)
            W = X
        else:
            X = np.random.default_rng(seed).standard_normal((n, 1))
            W = None

        # Cross-fit folds bounded by per-class count, same logic as
        # the LinearDR path so the two estimators see comparable
        # bias-variance trade-offs on small eval-gate datasets.
        per_class = min(int(T_bin.sum()), int((1 - T_bin).sum()))
        cv_folds = max(2, min(3, per_class // 2)) if per_class >= 4 else 2

        # Calibrated GBC for propensity: GBC handles non-linearity that
        # plain LogisticRegression would miss, CalibratedClassifierCV
        # rescues the predict_proba distribution back from the 0/1
        # boundary.
        propensity_model = CalibratedClassifierCV(
            GradientBoostingClassifier(
                n_estimators=50, max_depth=3, random_state=seed,
            ),
            method="isotonic",
            cv=cv_folds,
        )
        outcome_model = GradientBoostingRegressor(
            n_estimators=50, max_depth=3, random_state=seed,
        )

        est = ForestDRLearner(
            model_propensity=propensity_model,
            model_regression=outcome_model,
            # n_estimators must be divisible by ForestDRLearner's
            # default subforest_size=4 (the BLB sub-forest count).
            # 48 is the closest "small enough for the eval-gate" number
            # that satisfies n_estimators % 4 == 0.
            n_estimators=48,
            min_samples_leaf=max(10, n // 30),
            cv=cv_folds,
            random_state=seed,
        )
        est.fit(Y=Y, T=T_bin, X=X, W=W)

        # Per-row CATE — the whole point of the forest stage. ATE is
        # the population mean of CATEs; the distribution captures
        # heterogeneity the linear stage averages out.
        cates = est.effect(X, T0=0, T1=1)
        pt = float(np.mean(cates))
        # 10 evenly-spaced quantiles, 0.05..0.95 inclusive, at 6
        # decimals for canonical-JSON byte stability.
        q_pts = np.linspace(0.05, 0.95, 10)
        cate_quantiles = [
            round(float(q), 6)
            for q in np.quantile(cates, q_pts)
        ]

        # Forest CI via Bootstrap-of-Little-Bags. effect_interval works
        # without re-fitting because ForestDRLearner builds the BLB
        # samples during fit() when inference='blb' (the default).
        try:
            lo_arr, hi_arr = est.effect_interval(X, T0=0, T1=1, alpha=0.05)
            ci_lower = float(np.mean(lo_arr))
            ci_upper = float(np.mean(hi_arr))
        except Exception as ci_exc:  # pragma: no cover
            # Some EconML versions / inference modes fall back to no
            # CI when BLB can't be computed; surface a sentinel rather
            # than crash the estimate altogether.
            logger.warning("ForestDR CI computation failed: %s", ci_exc)
            ci_lower = pt
            ci_upper = pt
        if ci_upper < ci_lower:
            ci_lower, ci_upper = ci_upper, ci_lower

        if not (np.isfinite(pt) and np.isfinite(ci_lower) and np.isfinite(ci_upper)):
            raise ValueError(
                f"ForestDR-Learner produced non-finite output "
                f"(point={pt}, ci=[{ci_lower}, {ci_upper}])"
            )

        # Reuse the LinearDR's propensity-diagnostics capture helper
        # for parity — operator card propensity bar works the same way
        # for both DR estimators.
        propensity_diag = _compute_propensity_diagnostics(
            X=X, W=W, T_bin=T_bin, cv_folds=cv_folds, seed=seed,
        )

        # Sprint 16: optional conformal calibration overrides the BLB
        # CI when opted in AND the calibration set is large enough.
        # Same shape as the LinearDR conformal path — the helper
        # doesn't care whether the asymptotic CI came from a linear
        # sandwich or a forest BLB.
        ci_method: str = "asymptotic"
        if conformal_calibration:
            conformal_result = _conformal_ate_via_aipw(
                X=X, W=W, T_bin=T_bin, Y=Y,
                cv_folds=cv_folds, seed=seed,
            )
            if conformal_result is not None:
                pt = conformal_result["point"]
                ci_lower = conformal_result["ci_lower"]
                ci_upper = conformal_result["ci_upper"]
                ci_method = "conformal"

        return CounterfactualEstimate(
            method="forest_dr",
            point=pt,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            n_samples=n,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            propensity_diagnostics=propensity_diag,
            cate_distribution=cate_quantiles,
            ci_method=ci_method,  # type: ignore[arg-type]
        )
    except Exception as exc:
        logger.warning("ForestDR-Learner (econml) failed: %s", exc)
        return CounterfactualEstimate(
            method="forest_dr",
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


# ── Sprint 11: deterministic seeding ─────────────────────────────────

def _seed_for(request_hash: str, name: str) -> int:
    """Derive a stable 32-bit numpy seed from ``(request_hash, name)``.

    Same query (same request_hash) + same method/refuter name → same
    seed → same DoWhy random draws → byte-identical artifact_hash on
    re-execution.

    32-bit because numpy's seed is a uint32; the upper bits are masked
    off the sha256 prefix.
    """
    h = hashlib.sha256(f"{request_hash}|{name}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big")


def _seed_numpy(seed: int) -> None:
    """Pre-seed numpy's *global* RNG.

    DoWhy / scikit-learn use ``numpy.random`` directly in many paths
    (bootstrap CI, train/test split, propensity-score sampling) without
    accepting a ``random_state`` parameter. Seeding the global generator
    immediately before each call is the only way to pin those uses.

    This is **only safe under sequential execution** — concurrent threads
    would trample each other's seed. ``run_estimators`` and
    ``run_refuters`` therefore default to ``concurrent=False`` for
    determinism. Callers that explicitly want concurrent fan-out
    (sacrificing reproducibility) can opt in.
    """
    import numpy as np
    np.random.seed(seed)


# ── Causal-model construction ─────────────────────────────────────────

def _build_causal_model(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
) -> Any:
    if not _DOWHY_AVAILABLE:
        raise RuntimeError("dowhy is not installed in this environment")
    edges = dag.get("edges", [])
    edge_lines = "\n".join(f'  "{src}" -> "{dst}";' for src, dst in edges)
    graph = f'digraph {{\n{edge_lines}\n}}'
    return CausalModel(
        data=df,
        treatment=treatment.column,
        outcome=outcome.column,
        graph=graph,
    )


# ── Estimator fan-out ─────────────────────────────────────────────────

def _run_one_tmle(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    seed: int,
) -> CounterfactualEstimate:
    """Cross-fitted TMLE for ATE — Sprint S22.

    Pure-NumPy + sklearn implementation in ``tmle.py`` (no econml,
    no DoWhy). Achieves the semi-parametric efficiency bound that the
    DR-Learner slots fall short of in finite samples. See the module
    docstring there for the targeting-step math.

    Determinism: ``run_tmle_ate(seed=seed)`` re-seeds numpy and threads
    the seed into every stochastic component (KFold, LogisticRegression).
    LinearRegression is analytic. Same input + same seed → byte-
    identical output. Layer 10 contract.

    Propensity diagnostics: shaped the same as the LinearDR / ForestDR
    paths so the operator card renders propensity quantile bars
    uniformly across DR-class estimators.

    NO conformal_calibration plumbing yet — the TMLE asymptotic CI
    from the influence-curve variance estimator is the right
    publication CI here. A future S22.1 can wire conformal as an
    opt-in override for parity with the DR slots."""
    t0 = time.perf_counter()
    try:
        import numpy as np

        from counterfactual_service.tmle import run_tmle_ate

        _seed_numpy(seed)

        # Same shape as the LinearDR / ForestDR feature build: binarise
        # T against treatment.actual, use treatment parents from the DAG
        # as confounders, fall back to a synthetic 1-col matrix when
        # there are none.
        T_bin = (df[treatment.column] == treatment.actual).astype(int).to_numpy()
        Y = df[outcome.column].to_numpy(dtype=float)

        treatment_parents = sorted({
            src for src, dst in dag.get("edges", [])
            if dst == treatment.column
            and src != outcome.column
            and src in df.columns
        })
        if treatment_parents:
            X = df[treatment_parents].to_numpy(dtype=float)
        else:
            # No confounders declared — fit on a constant column so
            # the propensity model degenerates to the marginal P(T=1)
            # and TMLE reduces to a Hajek-style estimator. Surfaces
            # an estimate rather than failing on edge-case DAGs.
            X = np.ones((len(df), 1), dtype=float)

        result = run_tmle_ate(Y=Y, T=T_bin, X=X, seed=seed)

        if not (
            np.isfinite(result["point"])
            and np.isfinite(result["ci_lower"])
            and np.isfinite(result["ci_upper"])
        ):
            raise ValueError(
                f"TMLE produced non-finite output (point={result['point']}, "
                f"ci=[{result['ci_lower']}, {result['ci_upper']}])"
            )

        propensity_diag = PropensityDiagnostics(
            quantiles=result["g_quantiles"],
            min=result["g_min"],
            max=result["g_max"],
            mean=result["g_mean"],
            n_extreme=result["g_n_extreme"],
            n_total=result["n_samples"],
        )

        return CounterfactualEstimate(
            method="tmle",
            point=result["point"],
            ci_lower=result["ci_lower"],
            ci_upper=result["ci_upper"],
            n_samples=result["n_samples"],
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            propensity_diagnostics=propensity_diag,
        )
    except Exception as exc:
        logger.warning("TMLE failed: %s", exc)
        return CounterfactualEstimate(
            method="tmle",
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_one_estimator(
    method_key: EstimatorMethod,
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    seed: int = 0,
    conformal_calibration: bool = False,
) -> CounterfactualEstimate:
    # Sprint 12 dispatch: when econml is installed, route ``double_ml``
    # through the real DR-Learner. Without econml the slot stays on the
    # DoWhy backdoor.linear_regression stub below (semantically weaker
    # but still doubly-robust in the linear-DGP case the eval-gate hits).
    # Sprint 16: conformal_calibration threads through to the DR path
    # only — the DoWhy stub still ships an asymptotic CI.
    if method_key == "double_ml" and _ECONML_AVAILABLE:
        return _run_one_econml_dr_learner(
            df, treatment, outcome, dag, seed,
            conformal_calibration=conformal_calibration,
        )
    # Sprint 15 dispatch: ``forest_dr`` is opt-in (callers must include
    # it explicitly via methods=[...]); without econml it surfaces a
    # structured error rather than silently dropping the request.
    if method_key == "forest_dr":
        if _ECONML_AVAILABLE:
            return _run_one_econml_forest_dr_learner(
                df, treatment, outcome, dag, seed,
                conformal_calibration=conformal_calibration,
            )
        return CounterfactualEstimate(
            method="forest_dr",
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df), elapsed_ms=0.0,
            error="econml not installed — forest_dr unavailable",
        )
    # Sprint S22 dispatch: ``tmle`` is opt-in like forest_dr. NO econml
    # dependency — pure sklearn + numpy — so the slot is always
    # available when sklearn is installed (it already is, every DR
    # path depends on it). Achieves the semi-parametric efficiency
    # bound that DR-Learner falls short of in finite samples.
    if method_key == "tmle":
        return _run_one_tmle(df, treatment, outcome, dag, seed)

    # S31b dispatch: ``iv`` is opt-in. Pure-NumPy 2SLS, no dowhy/econml —
    # the instrument is read from the DAG (a node -> treatment, not ->
    # outcome). Absent instrument surfaces a structured error, not a crash.
    if method_key == "iv":
        from .iv_estimator import instruments_from_dag, run_iv_2sls
        iv_t0 = time.perf_counter()
        edges = dag.get("edges", [])
        instruments = instruments_from_dag(edges, treatment.column, outcome.column)
        confounders = [
            src for src, dst in edges
            if dst == outcome.column and src != treatment.column
            and src not in instruments
        ]
        try:
            if not instruments:
                raise ValueError("no instrument in DAG (need a node -> treatment, not -> outcome)")
            point, lo, hi = run_iv_2sls(
                df, treatment.column, outcome.column, instruments, sorted(set(confounders)),
            )
            return CounterfactualEstimate(
                method="iv", point=point, ci_lower=lo, ci_upper=hi,
                n_samples=len(df), elapsed_ms=(time.perf_counter() - iv_t0) * 1000,
            )
        except Exception as exc:
            return CounterfactualEstimate(
                method="iv", point=0.0, ci_lower=0.0, ci_upper=0.0,
                n_samples=len(df), elapsed_ms=(time.perf_counter() - iv_t0) * 1000,
                error=f"IV (2SLS) failed: {exc}",
            )

    t0 = time.perf_counter()
    try:
        # Pin the global numpy RNG immediately before DoWhy touches it.
        # Bootstrap CI + propensity-score fitting both use np.random
        # internally, so this is the only universal handle.
        _seed_numpy(seed)
        model = _build_causal_model(df, treatment, outcome, dag)
        identified = model.identify_effect(proceed_when_unidentifiable=True)
        # Re-seed *between* identify and estimate — identify_effect can
        # consume RNG entropy on some DAGs.
        _seed_numpy(seed)
        est = model.estimate_effect(
            identified,
            method_name=_DOWHY_ESTIMATOR_METHODS[method_key],
            test_significance=True,
            confidence_intervals=True,
        )
        point = float(est.value)
        ci_attr = getattr(est, "get_confidence_intervals", None)
        ci = None
        if callable(ci_attr):
            try:
                ci = ci_attr()
            except Exception:
                ci = None
        if ci is not None:
            try:
                if hasattr(ci, "tolist"):
                    ci = ci.tolist()
                if isinstance(ci, (list, tuple)) and len(ci) >= 2:
                    flat = ci[0] if isinstance(ci[0], (list, tuple)) else ci
                    lo, hi = float(flat[0]), float(flat[1])
                else:
                    raise ValueError("unexpected CI shape")
            except Exception:
                ci = None
        if ci is None:
            stderr = float(getattr(est, "stderr", 0.0) or 0.0)
            lo, hi = point - 2 * stderr, point + 2 * stderr
        if hi < lo:
            lo, hi = hi, lo
        return CounterfactualEstimate(
            method=method_key,
            point=point,
            ci_lower=lo,
            ci_upper=hi,
            n_samples=len(df),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        logger.warning("Estimator %s failed: %s", method_key, exc)
        return CounterfactualEstimate(
            method=method_key,
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_estimators(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    methods: Optional[List[EstimatorMethod]] = None,
    timeout_s: float = 30.0,
    *,
    request_hash: str = "",
    concurrent: bool = False,
    conformal_calibration: bool = False,
) -> List[CounterfactualEstimate]:
    """Run each estimator with a deterministic seed and per-step timeout.

    Always returns one ``CounterfactualEstimate`` per requested method;
    failures and timeouts are surfaced via the ``error`` field rather
    than raising. Output is sorted by method name for hash-stable
    artifacts.

    ``concurrent=False`` (the default) runs estimators **sequentially**
    so the per-method numpy seed isn't trampled by interleaved threads.
    This is the only way to make the artifact byte-stable across
    re-runs (eval-gate Layer 10). ``concurrent=True`` reverts to the
    legacy thread-pool fan-out — faster but not reproducible. The
    operator-tier UI with a single chat session uses sequential; only
    callers that have explicitly accepted non-determinism (currently
    none) should opt into concurrent.

    Sprint 16: ``conformal_calibration=True`` opts the DR-class
    estimators (``double_ml``, ``forest_dr``) into a split-conformal
    pass that produces a finite-sample distribution-free CI. Has no
    effect on DoWhy-routed methods (their CIs come from DoWhy's
    own machinery). When conformal calibration declines to certify
    coverage (small n, degenerate split), the asymptotic CI is
    retained and ``ci_method="asymptotic"`` flags the fallback.
    """
    chosen: List[EstimatorMethod] = methods or list(_DOWHY_ESTIMATOR_METHODS.keys())
    loop = asyncio.get_event_loop()

    async def _one(m: EstimatorMethod) -> CounterfactualEstimate:
        seed = _seed_for(request_hash, m)
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, _run_one_estimator, m, df, treatment, outcome, dag, seed,
                    conformal_calibration,
                ),
                timeout_s,
            )
        except asyncio.TimeoutError:
            return CounterfactualEstimate(
                method=m, point=0.0, ci_lower=0.0, ci_upper=0.0,
                n_samples=len(df), elapsed_ms=timeout_s * 1000,
                error=f"timeout after {timeout_s}s",
            )

    if concurrent:
        results = await asyncio.gather(*(_one(m) for m in chosen))
    else:
        results = [await _one(m) for m in chosen]

    # Sprint S23: attach VanderWeele-Ding E-value + Cinelli-Hazlett
    # robustness value to every successful estimate. Computed centrally
    # (rather than inside each wrapper) so the eight existing wrappers
    # stay untouched. outcome_sd and n_controls are functions of (df,
    # outcome, dag) — invariant across all estimator slots — so they
    # only need computing once per fan-out. Failed estimates (error
    # populated, point/CI are placeholder zeros) keep sensitivity=None
    # because the question "how strong a confounder?" isn't meaningful
    # when the estimator never produced a value to be confounded.
    _attach_sensitivity(results, df=df, treatment=treatment, outcome=outcome, dag=dag)
    return sorted(results, key=lambda e: e.method)


# ── Sprint S23: sensitivity attachment ───────────────────────────────

def _attach_sensitivity(
    estimates: List[CounterfactualEstimate],
    *,
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
) -> None:
    """Populate ``estimate.sensitivity`` in place for every successful
    estimate in ``estimates``.

    Computes outcome_sd from the outcome column once, derives n_controls
    from the DAG (treatment-parents excluding the outcome itself, intersected
    with df.columns — same convention as the DR / TMLE wrappers), then calls
    ``compute_sensitivity_report`` per estimate.

    Failure mode: any exception in the per-estimate compute is swallowed
    and that estimate's ``sensitivity`` stays ``None``. Sensitivity is
    *advisory* — the artifact must still seal even if a downstream
    sklearn / scipy quirk produces NaN.
    """
    try:
        import numpy as np
        y_arr = df[outcome.column].to_numpy(dtype=float)
        # ddof=1 to match the unbiased sample-SD convention every other
        # statistical step in the engine uses (statsmodels, sklearn).
        outcome_sd = float(np.std(y_arr, ddof=1)) if len(y_arr) > 1 else 0.0
    except Exception as exc:
        logger.warning("Sensitivity outcome_sd capture failed: %s", exc)
        return

    n_controls = sum(
        1 for src, dst in dag.get("edges", [])
        if dst == treatment.column
        and src != outcome.column
        and src in df.columns
    )

    for est in estimates:
        if est.error is not None:
            continue
        try:
            payload = compute_sensitivity_report(
                point=float(est.point),
                ci_lower=float(est.ci_lower),
                ci_upper=float(est.ci_upper),
                n_samples=int(est.n_samples),
                n_controls=n_controls,
                outcome_sd=outcome_sd,
            )
            est.sensitivity = SensitivityReport(**payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("Sensitivity attach failed for %s: %s", est.method, exc)


# ── Refuter fan-out ───────────────────────────────────────────────────

def _refuter_passed(refuter: RefuterName, baseline: float, refuted: float) -> bool:
    """Pass criterion is method-specific.

    * placebo: refuted estimate should be near zero (treatment shuffled,
      so any leftover effect is noise).
    * everything else: refuted estimate should stay close to baseline.

    Threshold is 20% of |baseline| or 0.1 absolute, whichever is larger
    — matches DoWhy convention and avoids divide-by-near-zero blow-ups.
    """
    threshold = max(abs(baseline) * 0.2, 0.1)
    if refuter == "placebo":
        return abs(refuted) < threshold
    return abs(refuted - baseline) < threshold


def _run_one_refuter(
    refuter_key: RefuterName,
    model: Any,
    identified: Any,
    baseline_estimate: Any,
    seed: int = 0,
) -> RefutationResult:
    t0 = time.perf_counter()
    try:
        # Refuters explicitly use random sampling (placebo treatment,
        # data subset, random common cause). Pin numpy global state
        # before calling and pass random_seed where DoWhy supports it.
        _seed_numpy(seed)
        try:
            result = model.refute_estimate(
                identified,
                baseline_estimate,
                method_name=_DOWHY_REFUTER_METHODS[refuter_key],
                random_seed=seed,
            )
        except TypeError:
            # Some DoWhy versions don't accept ``random_seed`` on
            # ``refute_estimate``; the numpy seed above still pins the
            # randomness for those installs.
            result = model.refute_estimate(
                identified,
                baseline_estimate,
                method_name=_DOWHY_REFUTER_METHODS[refuter_key],
            )
        new_value: Optional[float] = None
        new_attr = getattr(result, "new_effect", None)
        if new_attr is not None:
            try:
                new_value = float(new_attr)
            except Exception:
                new_value = None
        p_value: Optional[float] = None
        ref_result = getattr(result, "refutation_result", None)
        if isinstance(ref_result, dict):
            p_raw = ref_result.get("p_value")
            try:
                p_value = float(p_raw) if p_raw is not None else None
            except Exception:
                p_value = None
        baseline_val = float(getattr(baseline_estimate, "value", 0.0) or 0.0)
        passed = (
            _refuter_passed(refuter_key, baseline_val, new_value)
            if new_value is not None
            else False
        )
        return RefutationResult(
            refuter=refuter_key,
            estimate_after=new_value,
            p_value=p_value,
            passed=passed,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        logger.warning("Refuter %s failed: %s", refuter_key, exc)
        return RefutationResult(
            refuter=refuter_key,
            estimate_after=None, p_value=None,
            passed=False,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_refuters(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    refuters: Optional[List[RefuterName]] = None,
    timeout_s: float = 30.0,
    *,
    request_hash: str = "",
    concurrent: bool = False,
) -> List[RefutationResult]:
    """Run each refuter with a deterministic seed and per-step timeout.

    Default ``concurrent=False`` runs refuters sequentially so the
    per-refuter numpy seed isn't trampled by thread interleaving — the
    artifact_hash is then byte-stable across re-runs.
    """
    chosen: List[RefuterName] = refuters or list(_DOWHY_REFUTER_METHODS.keys())
    if not _DOWHY_AVAILABLE:
        # Sorted to match the happy-path return so callers can rely on
        # a single ordering invariant — alphabetical by refuter name.
        return sorted(
            [
                RefutationResult(refuter=r, passed=False, error="dowhy not installed")
                for r in chosen
            ],
            key=lambda r: r.refuter,
        )

    try:
        # Baseline estimate — also pinned. Bootstrap CI here would
        # otherwise leak entropy that downstream refuters consume.
        _seed_numpy(_seed_for(request_hash, "_baseline"))
        model = _build_causal_model(df, treatment, outcome, dag)
        identified = model.identify_effect(proceed_when_unidentifiable=True)
        baseline = model.estimate_effect(
            identified,
            method_name=_DOWHY_ESTIMATOR_METHODS["linear_regression"],
        )
    except Exception as exc:
        logger.warning("Baseline for refuters failed: %s", exc)
        # Same alphabetical ordering as the happy path — when CI catches
        # a DoWhy/networkx breakage the test_run_refuters_run_on_synthetic
        # assertion on sort order must still hold, otherwise the failure
        # mode reads as "two bugs" when it's really one.
        return sorted(
            [
                RefutationResult(refuter=r, passed=False,
                                 error=f"baseline failed: {type(exc).__name__}: {exc}")
                for r in chosen
            ],
            key=lambda r: r.refuter,
        )

    loop = asyncio.get_event_loop()

    async def _one(r: RefuterName) -> RefutationResult:
        seed = _seed_for(request_hash, r)
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, _run_one_refuter, r, model, identified, baseline, seed,
                ),
                timeout_s,
            )
        except asyncio.TimeoutError:
            return RefutationResult(
                refuter=r, passed=False,
                elapsed_ms=timeout_s * 1000,
                error=f"timeout after {timeout_s}s",
            )

    if concurrent:
        results = await asyncio.gather(*(_one(r) for r in chosen))
    else:
        results = [await _one(r) for r in chosen]
    return sorted(results, key=lambda r: r.refuter)


# ── Sprint 14: deterministic propensity warning ───────────────────────

# Same numbers [[feedback_propensity_calibration]] uses for "extreme"
# propensities — keeping them as module-level constants so the engine
# diagnostic and the development guideline stay in lock-step. If you
# tune these here, update the memory file too.
_PROPENSITY_EXTREME_LOW = 0.05
_PROPENSITY_EXTREME_HIGH = 0.95
_PROPENSITY_EXTREME_FRACTION = 0.10


def _propensity_warning_challenges(
    estimates: List[CounterfactualEstimate],
) -> List[AdversarialChallenge]:
    """Inspect each estimate's propensity_diagnostics and emit at most
    one high-severity challenge per estimator that looks IPW-fragile.

    Two trigger conditions, OR'd together:
      1. ``n_extreme / n_total > 0.10`` — at least 10% of rows had
         propensity scores in the IPW-fragile region [<0.05, >0.95]
         where the doubly-robust correction (T - e) / [e(1-e)] blows
         up.
      2. ``min(p05, 1 - p95) < 0.05`` — the bulk of the distribution
         already lives near the boundary; even rows that don't make
         the n_extreme count are contributing to a fragile estimate.

    Threshold copies are documented in feedback_propensity_calibration.

    Deterministic: same diagnostics → same challenge text. The text
    is the SAME across all estimators with the same diagnostic shape
    so the sort+hash is stable across runs.
    """
    out: List[AdversarialChallenge] = []
    for est in estimates:
        diag = est.propensity_diagnostics
        if diag is None or diag.n_total <= 0:
            continue
        frac_extreme = diag.n_extreme / diag.n_total
        p05 = diag.quantiles.get("p05")
        p95 = diag.quantiles.get("p95")
        boundary_distance = None
        if p05 is not None and p95 is not None:
            boundary_distance = min(p05, 1.0 - p95)

        bad_fraction = frac_extreme > _PROPENSITY_EXTREME_FRACTION
        bad_distribution = (
            boundary_distance is not None
            and boundary_distance < _PROPENSITY_EXTREME_LOW
        )
        if not (bad_fraction or bad_distribution):
            continue

        # The text is intentionally identifying (mentions method +
        # specific numbers) so an auditor scanning challenges can
        # tell which estimator + how bad. Numbers are formatted to a
        # fixed precision so canonical-JSON byte stability holds
        # across re-runs that share the same diagnostics.
        text = (
            f"Estimator '{est.method}' had IPW-fragile propensity "
            f"diagnostics: {diag.n_extreme}/{diag.n_total} rows "
            f"({frac_extreme:.1%}) outside "
            f"[{_PROPENSITY_EXTREME_LOW:.2f}, "
            f"{_PROPENSITY_EXTREME_HIGH:.2f}]; "
            f"distribution p05={diag.quantiles.get('p05', 0):.3f}, "
            f"p95={diag.quantiles.get('p95', 0):.3f}. "
            f"Treat CI width as a floor, not a ceiling."
        )
        out.append(
            AdversarialChallenge(
                text=text,
                severity="high",
                suggested_check=(
                    "Re-fit the propensity nuisance with a calibrated "
                    "classifier (LogisticRegression with L2, or "
                    "CalibratedClassifierCV) and verify that "
                    "(n_extreme / n_total) drops below 10%."
                ),
            )
        )
    return out


# ── Sprint S24: estimator-class disagreement auto-challenge ──────────

# The "2× the CI half-width" threshold below is the same factor the
# RV-vs-DR auto-challenge concept is anchored on (S23 sensitivity +
# S22 TMLE). Two estimators whose point estimates diverge by more
# than twice the conformal/asymptotic CI half-width are statistically
# inconsistent — at the stated coverage level, both intervals can't
# cover the true ATE simultaneously. Either nuisance misspecification
# (more likely on the DR-Learner family) or a positivity violation
# (where both can fail) is the most common cause.
_DISAGREEMENT_HALF_WIDTH_MULTIPLIER = 2.0


def _estimator_disagreement_challenges(
    estimates: List[CounterfactualEstimate],
) -> List[AdversarialChallenge]:
    """Sprint S24. Emit one high-severity AdversarialChallenge when the
    TMLE point estimate and the ForestDR point estimate diverge by more
    than 2× the larger conformal/asymptotic CI half-width.

    Anchors S14's `_propensity_warning_challenges` exactly:
      * Pure function of already-computed `estimates` — no I/O, no LLM.
      * Fixed-precision format strings (`{:.3f}`) so the challenge text
        is byte-stable across re-runs with the same inputs (Layer 10).
      * Calls into the same severity = "high" + suggested_check shape
        the operator-card renderer + audit-chain consumers expect.

    Why TMLE vs ForestDR specifically: these are AURA's two semi-
    parametric efficiency-bound-achieving estimators with very different
    final-stage assumptions (linear submodel vs honest random forest).
    Significant disagreement between them is one of the strongest
    signals that nuisance misspecification (likely the DR side) or
    positivity violation is at play. The DoWhy quartet (linear / IPW /
    PSM / DR stub) are weaker baselines — disagreement among them is
    less informative.

    Returns an empty list when:
      * Either TMLE or forest_dr is missing from the estimates list
        (operator didn't opt them in via `methods=[...]`).
      * Either estimate has a populated `error` field.
      * Either estimate's CI is degenerate (lower == upper).
      * The conformal half-width is missing AND the asymptotic half-
        width is zero (no signal to compare).

    Threshold: 2× the LARGER of the two estimators' CI half-widths.
    Using the larger gives the lower-disagreement-rate side the
    benefit of the doubt — we only fire when the gap exceeds the
    more-conservative estimator's own uncertainty by 2x.
    """
    by_method = {e.method: e for e in estimates}
    tmle = by_method.get("tmle")
    forest = by_method.get("forest_dr")
    if tmle is None or forest is None:
        return []
    if tmle.error is not None or forest.error is not None:
        return []

    def _half_width(est: CounterfactualEstimate) -> float:
        return max(0.0, (est.ci_upper - est.ci_lower) / 2.0)

    hw_tmle = _half_width(tmle)
    hw_forest = _half_width(forest)
    hw = max(hw_tmle, hw_forest)
    if hw <= 0.0:
        # No CI signal — can't decide if disagreement is significant.
        return []

    gap = abs(tmle.point - forest.point)
    threshold = _DISAGREEMENT_HALF_WIDTH_MULTIPLIER * hw
    if gap <= threshold:
        return []

    # Identify the "stronger CI contract" for the explanatory text.
    # When both are conformal, the contract is finite-sample
    # distribution-free; when one is asymptotic, the comparison is
    # only asymptotically valid. Either way the disagreement is
    # informative; we just label it accurately for the auditor.
    ci_contract = (
        "conformal"
        if tmle.ci_method == "conformal" and forest.ci_method == "conformal"
        else "asymptotic"
    )

    text = (
        f"Estimator-class disagreement: TMLE point={tmle.point:.3f} "
        f"(CI [{tmle.ci_lower:.3f}, {tmle.ci_upper:.3f}]) "
        f"vs ForestDR point={forest.point:.3f} "
        f"(CI [{forest.ci_lower:.3f}, {forest.ci_upper:.3f}]). "
        f"Gap {gap:.3f} exceeds "
        f"{_DISAGREEMENT_HALF_WIDTH_MULTIPLIER:.1f}× the larger "
        f"{ci_contract} CI half-width ({hw:.3f}). "
        "Linear-submodel vs non-parametric DR are pulled apart by "
        "either nuisance misspecification or a positivity violation."
    )

    return [
        AdversarialChallenge(
            text=text,
            severity="high",
            suggested_check=(
                "Inspect propensity diagnostics on both estimates "
                "(n_extreme / n_total). If both look fine, the "
                "outcome-stage nuisance is the likely culprit — "
                "try ForestDR with a deeper forest or LinearDR with "
                "a polynomial feature expansion and re-run."
            ),
        )
    ]


# ── End-to-end orchestration ──────────────────────────────────────────

def _dataset_fingerprint(df: pd.DataFrame) -> str:
    """Stable sha256 over (sorted columns, dtypes, head/tail rows, length).

    Two structurally-identical dataframes from different file paths will
    fingerprint identically; any column rename or dtype change produces a
    new fingerprint. Sufficient for replay's "did the dataset move?" check.
    """
    cols = sorted(df.columns.tolist())
    h = hashlib.sha256()
    h.update(",".join(cols).encode("utf-8"))
    for c in cols:
        h.update(str(df[c].dtype).encode("utf-8"))
    if len(df):
        h.update(canonical_dumps(df.head(3).to_dict(orient="records")).encode("utf-8"))
        h.update(canonical_dumps(df.tail(3).to_dict(orient="records")).encode("utf-8"))
    h.update(str(len(df)).encode("utf-8"))
    return h.hexdigest()


async def _run_critic(
    estimates: List[CounterfactualEstimate],
    refutations: List[RefutationResult],
    dag: dict,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    *,
    request_hash: str,
) -> tuple[List[AdversarialChallenge], bool]:
    """Run the adversarial critic, with replay-determinism caching.

    Returns ``(challenges, regenerated)`` where ``regenerated`` is True
    if the critic re-ran (cache miss). Replay flows through cache hits
    so the artifact byte-rehashes identically.
    """
    # Identify the model so the cache key is sensitive to provider drift.
    # Agent-side late import avoids dragging shared.budget into module
    # init.
    from agents.base import AgentContext
    from agents.specialists.adversarial_critic_agent import AdversarialCriticAgent

    agent = AdversarialCriticAgent()
    model_id = getattr(agent.llm, "model", "") or ""
    model_version = getattr(agent.llm, "model_version", "") or "v1"

    cache_k = critic_cache.cache_key(
        request_hash=request_hash, model_id=str(model_id), model_version=str(model_version),
    )
    cached = critic_cache.get(cache_k)
    if cached is not None:
        return [AdversarialChallenge(**c) for c in cached], False

    ctx = AgentContext(
        user_prompt="critique counterfactual",
        task_description="Find missing confounders, identifiability failures, and "
                         "estimator-refutation contradictions.",
        upstream_results={
            "estimates": [e.model_dump() for e in estimates],
            "refutations": [r.model_dump() for r in refutations],
            "dag": dag,
            "treatment": treatment.model_dump(),
            "outcome": outcome.model_dump(),
        },
    )
    res = await agent.execute(ctx)
    raw = res.output.get("challenges", []) if res.succeeded else []

    # Persist into the cache so future replays hit it. This is a
    # best-effort write; a cache miss next time is recoverable as long
    # as the engine re-runs and the new bytes match.
    try:
        critic_cache.put(cache_k, raw)
    except Exception as exc:  # pragma: no cover
        logger.warning("Critic cache write failed (non-fatal): %s", exc)

    return [AdversarialChallenge(**c) for c in raw], True


_HASH_EXCLUDE_FIELDS: Dict[str, Any] = {
    "audit_record_hash": True,
    "rendered": True,
    "signature_b64": True,
    "signature_status": True,
    "signing_key_source": True,
    # record_id is uuid-random and uncorrelated with the inputs — exclude
    # so two jobs with identical inputs produce the same artifact_hash
    # regardless of the random ID assigned at submission time.
    "record_id": True,
    # regenerated_critic is *metadata about how the answer was produced*
    # (cache hit vs miss), not part of the answer itself. Excluding it
    # means the artifact hash is byte-stable across replay regardless of
    # whether the critic-cache survived since the original sealing.
    "regenerated_critic": True,
    # elapsed_ms is wallclock per-step timing — pure metadata, drifts
    # every run. Strip from every estimate and every refutation so the
    # re-execution Layer 10 contract holds. Pydantic v2 ``__all__`` key
    # applies the exclude to every list element.
    "estimates":   {"__all__": {"elapsed_ms"}},
    "refutations": {"__all__": {"elapsed_ms"}},
}


def strip_for_hashing(artifact: Any) -> Dict[str, Any]:
    """Return the payload dict used for both signing AND verification.

    Single source of truth so the sign-time bytes (engine.run_job) and
    the verify-time bytes (main.verify_artifact) can never drift out of
    sync. Accepts either a Pydantic ``CounterfactualArtifact`` instance
    or a dict (read back from persistence) and applies the exclude spec
    through ``model_dump`` so nested rules like
    ``{"estimates": {"__all__": {"elapsed_ms"}}}`` are honoured uniformly.

    The bug this prevents: Sprint 11 added the nested exclude for
    per-element elapsed_ms. The engine sign path used model_dump
    correctly; the verify path used a flat dict-comprehension that
    only stripped top-level keys. Result was verified=False on every
    Sprint 11+ signed artifact because the reconstructed payload still
    had elapsed_ms inside each estimate/refutation, so the canonical
    bytes diverged from what had actually been signed.
    """
    if isinstance(artifact, CounterfactualArtifact):
        return artifact.model_dump(mode="json", exclude=_HASH_EXCLUDE_FIELDS)
    # dict path: re-parse through Pydantic so the exclude spec applies
    # with the same nested semantics. Extra fields are dropped, missing
    # ones fail closed — that's deliberate, we want a structural mismatch
    # to surface as ValidationError rather than silently produce bytes
    # that differ from what was signed.
    return CounterfactualArtifact.model_validate(artifact).model_dump(
        mode="json", exclude=_HASH_EXCLUDE_FIELDS,
    )


def _request_hash(query: CounterfactualQuery, dataset_fingerprint: str) -> str:
    """Stable hash of the user-controllable inputs.

    Used as the cache key for the critic and as the seed-derivation
    base. Must NOT depend on record_id, audit_record_hash, or anything
    populated downstream by the engine.
    """
    return sha256_canonical({
        "query": query.model_dump(mode="json"),
        "dataset_fingerprint": dataset_fingerprint,
    })


async def run_job(
    query: CounterfactualQuery,
    df: pd.DataFrame,
    methods: Optional[List[EstimatorMethod]] = None,
) -> CounterfactualArtifact:
    """Full engine: estimate → refute → critique (cached) → score → sign → persist → seal.

    Returns the artifact with ``audit_record_hash`` and (when signing is
    available) ``signature_b64`` populated. Caller is responsible for
    renderer dispatch (engine is renderer-agnostic).
    """
    # Defensive copy — DoWhy's PSM and IPW estimators mutate the input
    # DataFrame (attach propensity scores, weights, matched-pair labels),
    # which would change dataset_fingerprint on a subsequent run with the
    # same logical input.
    df = df.copy()
    fingerprint = _dataset_fingerprint(df)
    req_hash = _request_hash(query, fingerprint)

    estimates = await run_estimators(
        df, query.treatment, query.outcome, query.dag.model_dump(),
        methods=methods,
        request_hash=req_hash,
    )
    refutations = await run_refuters(
        df, query.treatment, query.outcome, query.dag.model_dump(),
        request_hash=req_hash,
    )
    challenges_unsorted, regenerated = await _run_critic(
        estimates, refutations, query.dag.model_dump(),
        query.treatment, query.outcome,
        request_hash=req_hash,
    )
    # Sprint 14: deterministic propensity check. If any estimator surfaced
    # cross-fitted propensity diagnostics that look IPW-fragile, append a
    # high-severity challenge BEFORE the sort+hash so it's part of the
    # audit basis. This is non-LLM and non-cached — pure function of the
    # already-computed diagnostics — so it can't drift across replay and
    # doesn't depend on the critic-cache survival. See
    # [[feedback_propensity_calibration]] for the 0.05 / 0.95 / 10%
    # threshold rationale.
    challenges_unsorted.extend(
        _propensity_warning_challenges(estimates),
    )
    # Sprint S24: estimator-class disagreement check — runs after S14's
    # propensity check so the operator card sees them in stable order
    # post-sort. Same shape, same byte-stability, same hash-basis
    # discipline: deterministic function of (estimates,) only.
    challenges_unsorted.extend(
        _estimator_disagreement_challenges(estimates),
    )
    # SHA-1 is only used as a stable, deterministic tie-breaker on the
    # challenge text — purely so two artifacts with identical (severity,
    # text) lists sort identically across runs. ``usedforsecurity=False``
    # signals to security scanners (bandit) that this is not a
    # cryptographic use and silences the B324 warning.
    challenges = sorted(
        challenges_unsorted,
        key=lambda c: (
            c.severity,
            hashlib.sha1(c.text.encode("utf-8"), usedforsecurity=False).hexdigest(),
        ),
    )

    record_id = f"ca_{uuid.uuid4().hex[:12]}"
    schema_version = "v1"   # Sprint 10: derive from current alembic head

    artifact = CounterfactualArtifact(
        record_id=record_id,
        query=query,
        estimates=estimates,
        refutations=refutations,
        challenges=challenges,
        confidence=score_confidence(estimates, refutations, challenges),
        schema_version=schema_version,
        dataset_fingerprint=fingerprint,
        regenerated_critic=regenerated,
    )

    # Compute artifact_hash over the artifact MINUS audit/render/signature
    # fields. record_id is also excluded so byte-stable replay is possible
    # regardless of which ca_<uuid> the original submission was assigned.
    # Goes through strip_for_hashing so verify_artifact builds identical
    # bytes — Sprint 13 fix for the verify-vs-sign drift.
    payload = strip_for_hashing(artifact)
    artifact_hash = sha256_canonical(payload)
    artifact.audit_record_hash = artifact_hash

    # Sign the canonical bytes of the (still-hash-stable) payload. The
    # signed bytes are exactly what sha256_canonical hashed, so a verifier
    # can independently reconstruct what was signed.
    sig_b64 = signing.sign_bytes(canonical_dumps(payload).encode("utf-8"))
    if sig_b64 is not None:
        artifact.signature_b64 = sig_b64
        artifact.signature_status = "signed"
        artifact.signing_key_source = signing.signing_key_source()
    else:
        artifact.signature_status = "unsigned"

    # Persist the full artifact (with audit_record_hash + signature) so
    # replay returns byte-identical content.
    try:
        full_persistable = artifact.model_dump(mode="json")
        persistence.write_artifact(artifact_hash, full_persistable)
        if sig_b64 is not None:
            persistence.write_signature(artifact_hash, sig_b64)
    except Exception as exc:  # pragma: no cover
        logger.warning("Artifact persistence failed (non-fatal): %s", exc)

    # Seal in TRAIGA audit log (best-effort; engine never blocks on audit).
    try:
        from shared.audit_log import AUDIT_ENABLED  # type: ignore
        if AUDIT_ENABLED:
            from shared.audit_log import audit_request  # type: ignore
            audit_request(
                user="counterfactual_service",
                method="POST",
                path="/counterfactual/jobs",
                meta={
                    "record_id": record_id,
                    "artifact_hash": artifact_hash,
                    "schema_version": schema_version,
                    "dataset_fingerprint": fingerprint,
                    "signature_status": artifact.signature_status,
                    "regenerated_critic": regenerated,
                },
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("Audit seal failed (non-fatal): %s", exc)

    return artifact
