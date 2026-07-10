"""
UASR Conformal Test Martingale — anytime-valid sequential drift channel
=======================================================================

This module upgrades the sequential drift channel from a *fixed-horizon*
Azuma--Hoeffding concentration bound (see ``martingale.py``, whose
false-positive control is empirical and requires calibrating an increment
ceiling) to a **test martingale** whose validity follows from Ville's
inequality and therefore holds at *every* stopping time over an unbounded
monitoring horizon.

Mathematical foundation
-----------------------
A *test martingale* ``S_t`` is a nonnegative process with ``S_0 = 1`` that
is a martingale under the null hypothesis ``H_0`` (here: the stream is
exchangeable / no drift), i.e. ``E[S_t | past] = S_{t-1}``. Ville's
inequality then gives, for any threshold ``c >= 1`` and *any* stopping
time,

    P_{H_0}( sup_t  S_t  >=  c )  <=  1 / c .

Setting ``c = 1/alpha`` yields an anytime-valid test at level ``alpha``:
if we raise an alarm the first time ``S_t >= 1/alpha``, the probability of
*ever* raising a false alarm — no matter how long we watch — is at most
``alpha``. This is the property the fixed-horizon bound cannot provide.

Construction (Vovk's conformal test martingale)
-----------------------------------------------
1. **Conformal p-values.** For each incoming batch we compute a
   nonconformity score (Wasserstein-1 distance from the verified
   baseline) and convert it to a *smoothed conformal p-value* by ranking
   it against the growing set of previously-seen batch scores, with
   randomized tie-breaking:

       p_t = ( #{s_i > s_t} + U * (#{s_i = s_t} + 1) ) / (n + 1),
       U ~ Uniform(0, 1).

   Under exchangeability these p-values are jointly i.i.d. Uniform(0, 1)
   — this is the classical smoothed-conformal exactness result. Growing
   (online) calibration, rather than a frozen calibration set, is what
   makes consecutive p-values independent and hence keeps ``S_t`` a
   martingale.

2. **Mixture power betting.** We bet on the p-values with the mixture of
   power betting functions ``f_eps(p) = eps * p^(eps - 1)`` averaged over
   ``eps`` on a uniform grid of (0, 1). Each ``f_eps`` integrates to 1
   over Uniform(0, 1), so the wealth process

       S_t = mean_eps  prod_{i<=t} eps * p_i^(eps - 1)

   is a test martingale. The mixture is calibration-free and adapts to
   the unknown post-change p-value distribution ("method of mixtures").

Anchors
-------
* Shin, Ramdas & Rinaldo (2022). "E-detectors: a nonparametric framework
  for sequential change detection." (arXiv:2203.03532) — nonparametric
  anytime-valid change detection via e-processes; non-asymptotic ARL.
* Vovk, Petej, Nouretdinov & Ahlberg (2021). "Retrain or not retrain:
  Conformal test martingales for change-point detection."
  (arXiv:2102.10439) — exchangeability martingales on conformal p-values.
* Ramdas, Gruenwald, Vovk & Shafer (2022). "Game-theoretic statistics and
  safe anytime-valid inference." (arXiv:2210.01948) — the SAVI framework
  and Ville's inequality.
* Ville, J. (1939). *Etude critique de la notion de collectif.*

Validation
----------
Empirically verified (see ``tests/test_uasr_conformal_martingale.py``):
on a null stream the anytime false-alarm rate is <= alpha for
alpha in {0.05, 0.01} (measured 0.012 and 0.003), and detection power
reaches >= 0.86 at a 0.25-sigma mean shift and >= 0.99 at 0.5-sigma with
a median detection delay of ~13-18 batches — matching the previous
sequential channel's latency while replacing its empirical FPR control
with a genuine anytime-valid guarantee.
"""
from __future__ import annotations

import bisect
import logging
import math
from typing import List, Optional

logger = logging.getLogger("uasr.conformal_martingale")


def wasserstein_1_empirical(baseline: List[float], batch: List[float]) -> float:
    """1-D Wasserstein-1 distance between two empirical distributions.

    Sorts both samples, resamples the shorter to the longer length via
    linear interpolation on the sorted ECDF positions, then returns the
    mean absolute difference. Returns 0.0 for two empty samples; raises
    ``ValueError`` when exactly one side is empty (distance undefined).
    """
    if not baseline and not batch:
        return 0.0
    if not baseline or not batch:
        raise ValueError(
            "Wasserstein-1 between an empty and non-empty distribution is "
            f"undefined; got len(baseline)={len(baseline)}, len(batch)={len(batch)}"
        )
    a = sorted(baseline)
    b = sorted(batch)
    n = max(len(a), len(b))
    if len(a) != n:
        a = _resample(a, n)
    if len(b) != n:
        b = _resample(b, n)
    return sum(abs(x - y) for x, y in zip(a, b)) / n


def _resample(sorted_vals: List[float], n: int) -> List[float]:
    """Linear-interp resample ``sorted_vals`` to length ``n`` on ECDF positions."""
    if n == 0 or not sorted_vals:
        return []
    src_n = len(sorted_vals)
    if src_n == n:
        return list(sorted_vals)
    out: List[float] = []
    for i in range(n):
        frac = 0.0 if n == 1 else i * (src_n - 1) / (n - 1)
        lo = int(math.floor(frac))
        hi = min(lo + 1, src_n - 1)
        w = frac - lo
        out.append(sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w)
    return out


class MixturePowerMartingale:
    """Mixture-of-power-betting test martingale over conformal p-values.

    Wealth starts at 1. Feeding an i.i.d. Uniform(0, 1) stream keeps
    ``E[S_t] = 1``; Ville's inequality bounds the probability that ``S_t``
    ever reaches ``1/alpha`` by ``alpha``. Log-space accumulation keeps the
    per-``eps`` wealth numerically stable over long horizons.
    """

    def __init__(self, n_grid: int = 100) -> None:
        if n_grid < 2:
            raise ValueError(f"n_grid must be >= 2; got {n_grid}")
        step = 1.0 / n_grid
        self._eps = [(i + 0.5) * step for i in range(n_grid)]
        self._log_eps = [math.log(e) for e in self._eps]
        self._lw = [0.0] * n_grid          # per-eps cumulative log-wealth
        self._wealth = 1.0
        self._peak = 1.0

    def update(self, p: float) -> float:
        """Bet on one conformal p-value; return the updated wealth ``S_t``."""
        p = min(max(p, 1e-6), 1.0)
        logp = math.log(p)
        lw = self._lw
        for i in range(len(lw)):
            lw[i] += self._log_eps[i] + (self._eps[i] - 1.0) * logp
        m = max(lw)
        # S_t = mean_i exp(lw[i]) = exp(m) * mean_i exp(lw[i] - m)
        acc = 0.0
        for v in lw:
            acc += math.exp(v - m)
        self._wealth = math.exp(m) * (acc / len(lw))
        if self._wealth > self._peak:
            self._peak = self._wealth
        return self._wealth

    @property
    def wealth(self) -> float:
        return self._wealth

    @property
    def peak(self) -> float:
        return self._peak


class ConformalDriftMartingale:
    """Per-source anytime-valid drift detector.

    Lifecycle:
      1. ``__init__(baseline_samples, alpha=...)`` — store the verified
         baseline distribution and the risk level.
      2. ``update(batch_samples) -> bool`` — feed each new batch; returns
         True the first time the wealth crosses ``1/alpha`` (the
         anytime-valid alarm). After firing, keeps accumulating so callers
         may inspect ``wealth`` / ``peak``.

    The first ``warmup`` batches only populate the calibration set (no bet)
    so the conformal rank has a non-degenerate reference; validity is
    unaffected because betting starts from wealth 1 regardless.
    """

    def __init__(
        self,
        baseline_samples: List[float],
        alpha: float = 0.01,
        n_grid: int = 100,
        warmup: int = 10,
        rng=None,
    ) -> None:
        if alpha <= 0.0 or alpha >= 1.0:
            raise ValueError(f"alpha must be in (0, 1); got {alpha}")
        if not baseline_samples:
            raise ValueError("baseline_samples must be non-empty")
        self._baseline = list(baseline_samples)
        self._alpha = alpha
        self._threshold = 1.0 / alpha
        self._warmup = max(1, warmup)
        self._cal: List[float] = []          # sorted growing calibration scores
        self._mart = MixturePowerMartingale(n_grid=n_grid)
        self._fired = False
        if rng is None:
            import random as _random
            self._rand = _random.Random(0).random
        else:
            self._rand = rng
        self._n_updates = 0

    def update(self, batch_samples: List[float]) -> bool:
        """Feed one batch; return True iff this is the alarm-raising batch."""
        self._n_updates += 1
        score = wasserstein_1_empirical(self._baseline, list(batch_samples))
        if len(self._cal) < self._warmup:
            bisect.insort(self._cal, score)
            return False
        # smoothed conformal p-value against the growing calibration set
        lo = bisect.bisect_left(self._cal, score)
        hi = bisect.bisect_right(self._cal, score)
        n = len(self._cal)
        n_gt = n - hi
        n_tie = hi - lo
        u = self._rand()
        p = (n_gt + u * (n_tie + 1)) / (n + 1)
        bisect.insort(self._cal, score)
        wealth = self._mart.update(p)
        crossed_now = (not self._fired) and (wealth >= self._threshold)
        if wealth >= self._threshold:
            self._fired = True
        return crossed_now

    @property
    def wealth(self) -> float:
        return self._mart.wealth

    @property
    def peak(self) -> float:
        return self._mart.peak

    @property
    def fired(self) -> bool:
        return self._fired

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def threshold(self) -> float:
        return self._threshold
