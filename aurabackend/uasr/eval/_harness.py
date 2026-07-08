"""Shared generators, corruption injectors, and helpers for the UASR eval suite.

All randomness flows through an explicit ``numpy.random.Generator`` so every
experiment is reproducible from its seed. Batches are produced in the
``BatchPayload`` shape the detector consumes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

# Make the package importable when run as a plain script from anywhere.
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from uasr.models import BatchPayload  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── batch construction ──────────────────────────────────────────────────────
def make_batch(source_id: str, batch_id: str, cols: Dict[str, np.ndarray]) -> BatchPayload:
    """Column-dict → BatchPayload (row-oriented)."""
    n = len(next(iter(cols.values())))
    rows = [{k: float(v[i]) for k, v in cols.items()} for i in range(n)]
    return BatchPayload(source_id=source_id, batch_id=batch_id, rows=rows)


def healthy_numeric(rng: np.random.Generator, mu=50.0, sigma=5.0, n=500) -> np.ndarray:
    return rng.normal(mu, sigma, n)


# ── corruption injectors (the "faults" a data pipeline suffers) ──────────────
def corrupt_unit_scale(col: np.ndarray, factor: float) -> np.ndarray:
    """Unit/scale bug: whole column multiplied (cents-vs-dollars, s-vs-ms)."""
    return col * factor


def corrupt_mean_shift(col: np.ndarray, delta: float) -> np.ndarray:
    """Legitimate regime change: distribution genuinely moves (must NOT heal)."""
    return col + delta


def corrupt_variance(col: np.ndarray, scale: float) -> np.ndarray:
    """Variance blow-up around the same mean."""
    mu = col.mean()
    return (col - mu) * scale + mu


def corrupt_null_spike(col: np.ndarray, rng: np.random.Generator, frac: float) -> np.ndarray:
    """Inject NaNs (missing-data spike)."""
    out = col.copy()
    k = int(len(out) * frac)
    idx = rng.choice(len(out), k, replace=False)
    out[idx] = np.nan
    return out


CORRUPTIONS: Dict[str, Callable] = {
    "unit_x100": lambda c, rng: corrupt_unit_scale(c, 100.0),
    "unit_div100": lambda c, rng: corrupt_unit_scale(c, 0.01),
    "unit_sec_to_ms": lambda c, rng: corrupt_unit_scale(c, 1000.0),
    "mean_shift": lambda c, rng: corrupt_mean_shift(c, 15.0),
    "variance_blowup": lambda c, rng: corrupt_variance(c, 3.0),
}


def summarize(name: str, rows: List[dict], path: Path) -> None:
    """Write a CSV of result rows (list of flat dicts)."""
    import csv
    if not rows:
        return
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval="")
        w.writeheader()
        w.writerows(rows)
