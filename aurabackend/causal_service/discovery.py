"""
Causal root-cause attribution.

Two engines:

  * **DoWhy gcm** — fits a Structural Causal Model (one mechanism per node)
    on the training distribution, then calls ``gcm.attribute_anomalies`` to
    score how much each upstream cause contributed to the anomalous target
    value. Gated by a stationarity guardrail (ADF + split-mean drift): if
    the training window spans a regime change the request is refused
    rather than returning misleading attributions.

  * **Correlation fallback** — three-tier robust partial correlation:
    pingouin (Moore-Penrose pseudoinverse, the standard collinearity-safe
    implementation) → statsmodels OLS residuals → numpy with rank-deficiency
    detection. Used automatically when ``dowhy`` isn't installed or the
    caller passes ``method='correlation'``. Stationarity is not required
    here — partial correlation is descriptive, not generative.

Both return the same ``Attribution`` shape so the API is uniform.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .models import Attribution, StationarityVerdict

logger = logging.getLogger("aura.causal.discovery")


# ── Optional dep availability ─────────────────────────────────────────

try:
    import networkx as nx
    from dowhy import gcm  # type: ignore
    _DOWHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    nx = None  # type: ignore[assignment]
    gcm = None  # type: ignore[assignment]
    _DOWHY_AVAILABLE = False


try:
    import pingouin as pg  # type: ignore
    _PINGOUIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    pg = None  # type: ignore[assignment]
    _PINGOUIN_AVAILABLE = False


try:
    import statsmodels.api as sm  # type: ignore
    from statsmodels.tsa.stattools import adfuller  # type: ignore
    _STATSMODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    sm = None  # type: ignore[assignment]
    adfuller = None  # type: ignore[assignment]
    _STATSMODELS_AVAILABLE = False


def dowhy_available() -> bool:
    return _DOWHY_AVAILABLE


# ── Public engine selection ───────────────────────────────────────────

def attribute(
    training: pd.DataFrame,
    anomalies: pd.DataFrame,
    *,
    target: str,
    candidates: Sequence[str],
    edges: Optional[Sequence[Tuple[str, str]]],
    method: str,
    top_k: int,
    enforce_stationarity: bool = True,
) -> Tuple[List[Attribution], str, List[str], Optional[StationarityVerdict]]:
    """
    Returns ``(attributions sorted desc by score, method_used, warnings,
    stationarity_verdict)``. The verdict is populated only for the gcm
    engine — partial correlation is descriptive and doesn't require
    stationarity.
    """
    warnings: List[str] = []

    # Pre-filter: numeric only.  DoWhy fits continuous mechanisms by
    # default and a stray string column blows up auto-assignment.
    numeric_cands = [c for c in candidates if pd.api.types.is_numeric_dtype(training[c])]
    dropped = sorted(set(candidates) - set(numeric_cands))
    if dropped:
        warnings.append(f"Dropped non-numeric candidate columns: {dropped}")

    if not numeric_cands:
        return [], "none", warnings + ["No numeric candidate causes available."], None

    # Method resolution
    chosen = method
    if method == "auto":
        chosen = "gcm" if _DOWHY_AVAILABLE else "correlation"
    elif method == "gcm" and not _DOWHY_AVAILABLE:
        warnings.append("dowhy not installed — falling back to correlation engine.")
        chosen = "correlation"

    # Stationarity guardrail (gcm only). Correlation engine is
    # location/scale-invariant for descriptive purposes — running it on
    # non-stationary data still yields a defensible ranking, just one
    # that should be interpreted as "association in the slice", not
    # "causal effect".
    verdict: Optional[StationarityVerdict] = None
    if chosen == "gcm" and enforce_stationarity:
        verdict = check_stationarity(training[target])
        if not verdict.stationary:
            warnings.extend(verdict.reasons)
            warnings.append(
                "Causal attribution refused: training window is non-stationary. "
                "Either narrow the window to a single regime, run the correlation "
                "engine with method='correlation', or override with "
                "enforce_stationarity=false (results will be meaningless)."
            )
            return [], "refused_non_stationary", warnings, verdict

    if chosen == "gcm":
        attrs = _gcm_attribute(training, anomalies, target, numeric_cands, edges, warnings)
    else:
        attrs = _correlation_attribute(training, anomalies, target, numeric_cands)

    attrs.sort(key=lambda a: a.score, reverse=True)
    return attrs[:top_k], chosen, warnings, verdict


# ── Stationarity guardrail ────────────────────────────────────────────

# ADF p-value threshold above which we declare non-stationarity. 0.05 is
# the usual statistics convention; tighten in your overlay if false
# negatives are more painful than false positives.
_ADF_ALPHA = 0.05
# Split-mean drift in pooled-std units. > 3 sigma between the first and
# second half of the training window indicates a likely regime change
# even when ADF passes (ADF tests for unit roots, not level shifts).
_DRIFT_SIGMA_LIMIT = 3.0
# Minimum sample size before either test is meaningful.
_STATIONARITY_MIN_SAMPLES = 30


def check_stationarity(series: pd.Series) -> StationarityVerdict:
    """ADF test + split-mean drift on a single column.

    Either failure trips the verdict. We run *both* because they catch
    different pathologies: ADF detects integrated/random-walk processes;
    the split-drift catches step changes (e.g. a metric that doubles
    halfway through the window) that pass ADF but still violate the
    constant-distribution assumption gcm fits its mechanisms under.
    """
    reasons: List[str] = []
    adf_p: Optional[float] = None
    drift: Optional[float] = None

    s = series.dropna()
    if len(s) < _STATIONARITY_MIN_SAMPLES:
        # Too small to test reliably — accept and let the caller proceed
        # rather than rejecting a small but legitimate window.
        return StationarityVerdict(
            stationary=True, adf_p_value=None, split_drift_sigma=None,
            reasons=[f"Sample size {len(s)} < {_STATIONARITY_MIN_SAMPLES} — stationarity not tested"],
        )

    # ── ADF test ──────────────────────────────────────────────────
    if _STATSMODELS_AVAILABLE:
        try:
            result = adfuller(s.to_numpy(dtype=float), autolag="AIC")
            adf_p = float(result[1])
            if adf_p > _ADF_ALPHA:
                reasons.append(
                    f"ADF p-value {adf_p:.4f} > {_ADF_ALPHA} — fails to reject unit root"
                )
        except Exception as exc:
            logger.debug("ADF failed (%s) — skipping ADF half of guardrail", exc)
    else:
        reasons.append("statsmodels not installed — ADF test skipped (only split-mean drift checked)")

    # ── Split-mean drift ──────────────────────────────────────────
    arr = s.to_numpy(dtype=float)
    mid = len(arr) // 2
    first, second = arr[:mid], arr[mid:]
    if first.std() > 0 and second.std() > 0:
        pooled_std = math.sqrt((first.std() ** 2 + second.std() ** 2) / 2)
        if pooled_std > 0:
            drift = float(abs(first.mean() - second.mean()) / pooled_std)
            if drift > _DRIFT_SIGMA_LIMIT:
                reasons.append(
                    f"split-mean drift {drift:.2f}σ > {_DRIFT_SIGMA_LIMIT}σ — "
                    "regime change suspected between training halves"
                )

    # ``stationary`` is True only if neither check fired a reason — but a
    # statsmodels-not-installed advisory shouldn't itself trip the guard.
    blocking = [r for r in reasons if "not installed" not in r]
    return StationarityVerdict(
        stationary=not blocking,
        adf_p_value=adf_p,
        split_drift_sigma=drift,
        reasons=reasons,
    )


# ── DoWhy gcm engine ──────────────────────────────────────────────────

def _gcm_attribute(
    training: pd.DataFrame,
    anomalies: pd.DataFrame,
    target: str,
    candidates: Sequence[str],
    edges: Optional[Sequence[Tuple[str, str]]],
    warnings: List[str],
) -> List[Attribution]:
    cols = list(candidates) + [target]
    train_df = training[cols].dropna()
    anom_df = anomalies[cols].dropna()

    if len(train_df) < 30:
        warnings.append(
            f"Training set has only {len(train_df)} complete rows — "
            "gcm mechanism fits will be unstable."
        )

    graph = nx.DiGraph()
    graph.add_nodes_from(cols)
    if edges:
        graph.add_edges_from(edges)
    else:
        # Default: every candidate is a direct parent of the target.
        # Caller can supply a richer DAG via ``edges`` when domain
        # knowledge is available.
        for c in candidates:
            graph.add_edge(c, target)

    scm = gcm.StructuralCausalModel(graph)
    gcm.auto.assign_causal_mechanisms(scm, train_df)
    gcm.fit(scm, train_df)

    raw = gcm.attribute_anomalies(scm, target_node=target, anomaly_samples=anom_df)
    # raw is dict[node_name → np.ndarray of length n_anomalies].
    out: List[Attribution] = []
    for node, scores in raw.items():
        if node == target:
            continue
        mean_score = float(_safe_mean(scores))
        # Confidence: 1 − coefficient of variation, clamped to [0, 1].
        cv = float(_safe_std(scores)) / max(abs(mean_score), 1e-9)
        confidence = max(0.0, min(1.0, 1.0 - cv))
        direction = _direction_from_correlation(train_df, node, target)
        out.append(Attribution(
            cause=node,
            score=abs(mean_score),
            confidence=confidence,
            direction=direction,
        ))
    return out


# ── Correlation fallback ──────────────────────────────────────────────

def _correlation_attribute(
    training: pd.DataFrame,
    anomalies: pd.DataFrame,
    target: str,
    candidates: Sequence[str],
) -> List[Attribution]:
    """
    Partial correlation: |corr(c, target | other candidates)|.
    Strips the shared-driver bias that plain Pearson would suffer from.
    """
    cols = list(candidates) + [target]
    df = training[cols].dropna()
    if df.empty or len(df) < 5:
        return [Attribution(cause=c, score=0.0, confidence=0.0, direction="unknown") for c in candidates]

    out: List[Attribution] = []
    for c in candidates:
        controls = [x for x in candidates if x != c]
        try:
            r = _partial_corr(df, c, target, controls)
        except Exception:
            r = 0.0
        # Anomaly-aware boost: if the candidate's anomalous value deviates
        # strongly from its training mean, weight it up. This gives the
        # naive engine some sensitivity to *which* cause moved during the
        # incident, not just which is generally most predictive.
        boost = _zscore_in_anomaly(training, anomalies, c)
        score = abs(r) * (1.0 + min(boost, 3.0) / 3.0)
        direction = "positive" if r > 0 else ("negative" if r < 0 else "unknown")
        # Confidence as |r| itself — bounded [0,1] and intuitive.
        out.append(Attribution(
            cause=c, score=score, confidence=abs(r), direction=direction,
        ))
    return out


def _partial_corr(df: pd.DataFrame, x: str, y: str, controls: List[str]) -> float:
    """Pearson partial correlation of (x, y) given ``controls``.

    Three-tier robust implementation:

      1. **pingouin.partial_corr** — uses Moore-Penrose pseudoinverse
         under the hood, the reference collinearity-safe implementation.
      2. **statsmodels OLS residuals** — same residualisation pattern as
         the prior hand-rolled code but statsmodels uses SVD-based pinv,
         which does not silently produce garbage when control columns are
         near-collinear.
      3. **numpy with explicit rank check** — last-resort fallback when
         neither pingouin nor statsmodels is installed. Uses
         ``np.linalg.matrix_rank`` to detect rank deficiency, falls back
         to ``np.linalg.pinv`` when the control matrix is singular.

    The prior code path (``np.linalg.lstsq`` with no rank check) silently
    produced wrong residuals when two control columns were highly
    correlated — the bug I called out in the architecture review.
    """
    if not controls:
        return _safe_pearson_series(df[x], df[y])

    # ── Tier 1: pingouin ──────────────────────────────────────────
    if _PINGOUIN_AVAILABLE:
        try:
            result = pg.partial_corr(data=df, x=x, y=y, covar=list(controls), method="pearson")
            if result is not None and "r" in result.columns:
                r = float(result.iloc[0]["r"])
                if not math.isnan(r):
                    return r
        except Exception as exc:
            logger.debug("pingouin partial_corr failed (%s) — falling to statsmodels", exc)

    # ── Tier 2: statsmodels OLS residuals ─────────────────────────
    if _STATSMODELS_AVAILABLE:
        try:
            return _partial_corr_via_statsmodels(df, x, y, controls)
        except Exception as exc:
            logger.debug("statsmodels partial_corr failed (%s) — falling to numpy", exc)

    # ── Tier 3: numpy with rank-deficiency guard ──────────────────
    return _partial_corr_via_numpy(df, x, y, controls)


def _safe_pearson_series(a: pd.Series, b: pd.Series) -> float:
    try:
        r = a.corr(b)
        return 0.0 if r is None or math.isnan(r) else float(r)
    except Exception:
        return 0.0


def _partial_corr_via_statsmodels(df: pd.DataFrame, x: str, y: str, controls: List[str]) -> float:
    Z = sm.add_constant(df[list(controls)].to_numpy(dtype=float), has_constant="add")
    rx = df[x].to_numpy(dtype=float)
    ry = df[y].to_numpy(dtype=float)
    # OLS uses SVD-based pinv internally — robust to near-collinear Z.
    rx_resid = rx - sm.OLS(rx, Z).fit().predict(Z)
    ry_resid = ry - sm.OLS(ry, Z).fit().predict(Z)
    if rx_resid.std() == 0 or ry_resid.std() == 0:
        return 0.0
    import numpy as np
    r = np.corrcoef(rx_resid, ry_resid)[0, 1]
    return 0.0 if math.isnan(r) else float(r)


def _partial_corr_via_numpy(df: pd.DataFrame, x: str, y: str, controls: List[str]) -> float:
    import numpy as np
    Z_raw = df[list(controls)].to_numpy(dtype=float)
    Z = np.column_stack([Z_raw, np.ones(len(Z_raw))])  # intercept column

    # Detect rank deficiency BEFORE solving — np.linalg.lstsq with rcond=None
    # silently returns least-norm solutions for rank-deficient systems,
    # which is the exact pathology we're guarding against.
    rank = np.linalg.matrix_rank(Z, tol=1e-8)

    def _resid(col: str) -> "np.ndarray":
        t = df[col].to_numpy(dtype=float)
        if rank < Z.shape[1]:
            # Rank-deficient → use pinv with explicit cutoff so collinear
            # controls don't blow up the residuals.
            beta = np.linalg.pinv(Z, rcond=1e-8) @ t
        else:
            beta, *_ = np.linalg.lstsq(Z, t, rcond=None)
        return t - Z @ beta

    rx = _resid(x)
    ry = _resid(y)
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    r = np.corrcoef(rx, ry)[0, 1]
    return 0.0 if math.isnan(r) else float(r)


def _zscore_in_anomaly(training: pd.DataFrame, anomalies: pd.DataFrame, col: str) -> float:
    if col not in anomalies.columns or anomalies[col].dropna().empty:
        return 0.0
    mu = training[col].mean()
    sigma = training[col].std()
    if sigma == 0 or math.isnan(sigma):
        return 0.0
    return abs((anomalies[col].mean() - mu) / sigma)


# ── Tiny stat helpers (avoid hard numpy import at module load) ────────

def _safe_mean(xs: Iterable[float]) -> float:
    xs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else 0.0


def _safe_std(xs: Iterable[float]) -> float:
    xs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _direction_from_correlation(df: pd.DataFrame, c: str, target: str) -> str:
    try:
        r = df[[c, target]].corr().iloc[0, 1]
        if pd.isna(r) or r == 0:
            return "unknown"
        return "positive" if r > 0 else "negative"
    except Exception:
        return "unknown"


# ── Summary string ────────────────────────────────────────────────────

def summarise(attrs: List[Attribution], target: str, method: str) -> str:
    if method == "refused_non_stationary":
        return (
            f"Refused causal attribution for {target}: training window is "
            f"non-stationary (regime change detected). See `stationarity` "
            f"field for the failed checks."
        )
    if not attrs:
        return f"No causal attributions surfaced for {target} (method={method})."
    top = attrs[0]
    second = attrs[1] if len(attrs) > 1 else None
    bits = [
        f"Top driver of the {target} anomaly is **{top.cause}** "
        f"({top.direction} effect, score={top.score:.3f}, "
        f"confidence={top.confidence:.2f})."
    ]
    if second:
        bits.append(
            f"Secondary driver: {second.cause} (score={second.score:.3f})."
        )
    bits.append(f"Engine: {method}.")
    return " ".join(bits)
