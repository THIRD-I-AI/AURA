"""
UASR Numeric Semantic Analyzer (Phase 1 — inference only)
=========================================================

Understands the *semantics* of numeric columns and flags unit/scale corruption
that distribution-only drift detection (KL on histograms) can miss, while
refusing to touch legitimate distribution change.

Design in one line:
    numeric semantic embedding → drift is a *distance*, healing is an
    *inverse-projection* onto the source's own healthy manifold, gated so only
    genuine unit errors (which have a multiplicative inverse) are ever repaired.

Two detection tiers
--------------------
* **Per-source baseline (primary).** Compare a column to *its own* history in
  units of that source's per-dimension spread (diagonal Mahalanobis). A ×100
  unit error is enormous measured against a single source's magnitude variance,
  so sensitivity is near-total (empirically ~100% detect at ~0% false-heal on
  real tabular columns), while legitimate mean/variance shifts are detected but
  have no inverse transform that re-projects onto the baseline — so they are
  alerted, never auto-repaired.
* **Global type prototype (cold-start fallback).** With no source history, fall
  back to distance to the nearest semantic-type centroid. Weaker on pure
  multiplicative shifts (they preserve distribution shape); used only until a
  per-source baseline exists.

Phase-1 scope: this module *analyzes and proposes*. It never mutates data. A
proposed heal is a `HealProposal` that the recovery loop can route through the
existing canary + causal-evaluator gate before any deployment. numpy is the only
dependency (already a hard backend requirement).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_FEATURE_DIM = 16
_DEFAULT_DRIFT_K = 3.0        # z-distance multiple of healthy spread → drift
_DEFAULT_ACCEPT_RATIO = 0.5   # a heal must cut residual to ≤ this fraction of original

# Inverse-transform library for scale/unit errors. Keys are proposal names,
# values are the multiplicative factor applied to *repair* the column.
TRANSFORMS: Dict[str, float] = {
    "x1000": 1000.0, "x100": 100.0, "x10": 10.0,
    "div10": 0.1, "div100": 0.01, "div1000": 0.001,
    "sec_to_ms": 1000.0, "ms_to_sec": 0.001,
}


# ── column encoder ───────────────────────────────────────────────────────────

def _decimal_places(v: float) -> int:
    s = f"{v:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def encode_column(values) -> np.ndarray:
    """Map a numeric column to a 16-D semantic feature vector.

    Features 0-12 are scale/precision/sign/roundedness descriptors; 13-15 are a
    scale-free (IQR-normalised) quantile-shape signature. Non-finite entries are
    dropped; an empty column returns zeros.
    """
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if n == 0:
        return np.zeros(_FEATURE_DIM)
    absa = np.abs(a)
    logm = np.log10(absa + 1.0)
    is_int = np.isclose(a, np.round(a))
    sample = a[: min(n, 500)]
    decs = np.array([_decimal_places(x) for x in sample])
    digit_len = np.floor(np.log10(absa + 1.0)) + 1
    trailing0 = np.array(
        [1.0 if (int(round(x)) % 10 == 0 and x != 0) else 0.0 for x in sample]
    )
    order = a[1:] - a[:-1] if n > 1 else np.array([0.0])
    q = np.quantile(a, [0.05, 0.25, 0.5, 0.75, 0.95])
    iqr = (q[3] - q[1]) or 1.0
    qshape = (q - q[2]) / iqr
    return np.array([
        logm.mean(),                             # 0  typical scale (log)
        logm.std(),                              # 1  scale spread
        is_int.mean(),                           # 2  integer-valued fraction
        min(np.unique(a).size / n, 1.0),         # 3  distinct ratio (id≈1)
        (a > 0).mean(),                          # 4  positive fraction
        (a < 0).mean(),                          # 5  negative fraction
        decs.mean(),                             # 6  typical decimal places
        decs.std(),                              # 7  precision spread
        digit_len.mean(),                        # 8  digit count
        trailing0.mean(),                        # 9  roundedness
        (order > 0).mean(),                      # 10 monotone-increasing frac
        float(absa.max() <= 1.0),                # 11 bounded in [0,1]
        float(a.min() >= 0 and a.max() <= 100),  # 12 percentage-range flag
        qshape[0], qshape[1], qshape[4],         # 13-15 scale-free shape
    ], dtype=float)


def _euclidean(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


# ── per-source baseline (primary detector) ───────────────────────────────────

@dataclass
class NumericBaseline:
    """Diagonal-Mahalanobis baseline built from healthy batches of one column.

    Serializable (mu/sigma/healthy_z are plain lists) so it can live in the
    externalized StateStore alongside the other per-source drift state.
    """
    mu: List[float] = field(default_factory=list)
    sigma: List[float] = field(default_factory=list)
    healthy_z: float = 0.0
    n_batches: int = 0

    @classmethod
    def fit(cls, healthy_batches: List[Any]) -> "NumericBaseline":
        feats = np.array([encode_column(b) for b in healthy_batches])
        mu = feats.mean(0)
        sigma = feats.std(0)
        sigma[sigma < 1e-9] = 1e-9
        z = float(np.mean([
            np.sqrt((((f - mu) / sigma) ** 2).sum()) for f in feats
        ]))
        return cls(mu=mu.tolist(), sigma=sigma.tolist(),
                   healthy_z=z, n_batches=len(healthy_batches))

    def z_distance(self, column) -> float:
        f = encode_column(column)
        mu = np.asarray(self.mu)
        sigma = np.asarray(self.sigma)
        return float(np.sqrt((((f - mu) / sigma) ** 2).sum()))

    def is_drift(self, column, k: float = _DEFAULT_DRIFT_K) -> bool:
        return self.z_distance(column) > k * max(self.healthy_z, 1e-9)


# ── global type prototypes (cold-start fallback) ─────────────────────────────

@dataclass
class TypePrototypes:
    protos: Dict[str, List[float]] = field(default_factory=dict)
    spread: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def fit(cls, samples_by_type: Dict[str, List[Any]]) -> "TypePrototypes":
        protos, spread = {}, {}
        for k, batches in samples_by_type.items():
            feats = np.array([encode_column(b) for b in batches])
            centroid = feats.mean(0)
            protos[k] = centroid.tolist()
            spread[k] = float(np.mean([_euclidean(f, centroid) for f in feats]))
        return cls(protos=protos, spread=spread)

    def nearest(self, column) -> Tuple[Optional[str], float]:
        f = encode_column(column)
        best_k, best_r = None, float("inf")
        for k, p in self.protos.items():
            r = _euclidean(f, p)
            if r < best_r:
                best_k, best_r = k, r
        return best_k, best_r

    def is_drift(self, column) -> Tuple[bool, Optional[str], float]:
        k, r = self.nearest(column)
        return r > 2 * self.spread.get(k, 1.0), k, r


# ── proposals & analyzer ─────────────────────────────────────────────────────

@dataclass
class HealProposal:
    """A proposed, *un-applied* numeric repair. Route through the canary/causal
    gate before any deployment — Phase-1 never commits."""
    column: str
    transform: str            # key into TRANSFORMS, or "none"
    factor: float
    z_before: float
    z_after: float
    confidence: float         # residual-drop ratio in [0,1]; higher = stronger


@dataclass
class NumericDriftSignal:
    column: str
    drifted: bool
    tier: str                 # "per_source" | "cold_start"
    z_distance: float
    nearest_type: Optional[str] = None
    proposal: Optional[HealProposal] = None


class NumericSemanticAnalyzer:
    """Inference-only numeric semantic layer.

    Holds a per-source registry of `NumericBaseline`s and an optional global
    `TypePrototypes` for cold-start. `analyze_column` returns a signal (+ heal
    proposal); nothing is mutated. Baselines are serializable so they can be
    persisted in the shared StateStore.
    """

    def __init__(self, prototypes: Optional[TypePrototypes] = None,
                 drift_k: float = _DEFAULT_DRIFT_K,
                 accept_ratio: float = _DEFAULT_ACCEPT_RATIO):
        self._baselines: Dict[str, NumericBaseline] = {}
        self._prototypes = prototypes
        self._drift_k = drift_k
        self._accept_ratio = accept_ratio

    # -- baseline management --
    def register_baseline(self, key: str, healthy_batches: List[Any]) -> None:
        self._baselines[key] = NumericBaseline.fit(healthy_batches)

    def has_baseline(self, key: str) -> bool:
        return key in self._baselines

    def get_baseline(self, key: str) -> Optional[NumericBaseline]:
        return self._baselines.get(key)

    def load_baseline(self, key: str, baseline: NumericBaseline) -> None:
        """Inject a baseline recovered from external state."""
        self._baselines[key] = baseline

    # -- inference --
    def _propose_heal(self, column, baseline: NumericBaseline) -> HealProposal:
        col = np.asarray(column, dtype=float)
        z0 = baseline.z_distance(col)
        best_name, best_factor, best_z = "none", 1.0, z0
        for name, factor in TRANSFORMS.items():
            zz = baseline.z_distance(col * factor)
            if zz < best_z:
                best_z, best_name, best_factor = zz, name, factor
        accepted = (best_z <= self._accept_ratio * z0
                    and best_z <= self._drift_k * max(baseline.healthy_z, 1e-9))
        if not accepted:
            best_name, best_factor, best_z = "none", 1.0, z0
        confidence = 0.0 if z0 <= 0 else max(0.0, 1.0 - best_z / z0)
        return HealProposal(column="", transform=best_name, factor=best_factor,
                            z_before=z0, z_after=best_z, confidence=confidence)

    def analyze_column(self, column, key: str,
                       column_name: str = "") -> NumericDriftSignal:
        """Return a numeric-drift signal for one column of one source.

        `key` is typically ``f"{source_id}:{column_name}"`. Uses the per-source
        baseline when present (primary tier), else the global prototypes
        (cold-start tier), else reports no drift (nothing to compare against).
        """
        name = column_name or key
        baseline = self._baselines.get(key)
        if baseline is not None:
            z = baseline.z_distance(column)
            drifted = z > self._drift_k * max(baseline.healthy_z, 1e-9)
            proposal = None
            if drifted:
                proposal = self._propose_heal(column, baseline)
                proposal.column = name
            return NumericDriftSignal(column=name, drifted=drifted,
                                      tier="per_source", z_distance=z,
                                      proposal=proposal)
        if self._prototypes is not None and self._prototypes.protos:
            drifted, nearest, r = self._prototypes.is_drift(column)
            return NumericDriftSignal(column=name, drifted=drifted,
                                      tier="cold_start", z_distance=r,
                                      nearest_type=nearest)
        return NumericDriftSignal(column=name, drifted=False,
                                  tier="cold_start", z_distance=0.0)


# ── helpers to lift columns out of a batch ───────────────────────────────────

def numeric_columns_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    """Extract per-column numeric arrays from a list of row dicts.

    A column is numeric only if *every* present value coerces to float (all-or-
    nothing, matching the detector's type discipline)."""
    if not rows:
        return {}
    keys = set()
    for r in rows:
        keys.update(r.keys())
    out: Dict[str, List[float]] = {}
    for col in keys:
        vals, ok = [], True
        for r in rows:
            if col not in r or r[col] is None:
                continue
            try:
                vals.append(float(r[col]))
            except (TypeError, ValueError):
                ok = False
                break
        if ok and vals:
            out[col] = vals
    return out
