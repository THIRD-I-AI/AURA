"""
UASR Latent Drift Detector
============================
Monitors the statistical manifold of incoming data batches.

Detection methods:
  1. Schema drift  — column additions / removals / type changes
  2. Statistical drift — KL-Divergence between batch P and baseline Q
  3. Semantic drift  — cosine distance between batch embedding and reference matrix

KL Divergence:
    D_KL(P || Q) = Σ P(x) · log(P(x) / Q(x))

Dynamic threshold ζ adapts based on historical variance of D_KL values.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import re
import threading
import time
import zlib
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np

from .models import (
    BatchPayload,
    ColumnDistribution,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    schema_types_match,
)
from .state_store import InMemoryStateStore, SourceState, StateStore

logger = logging.getLogger("uasr.drift_detector")

# Small constant to prevent log(0)
_EPS = 1e-10

# Divergence reported when a column is numeric on one side and categorical on the other.
_KIND_CHANGE_KL = -math.log(_EPS)

# Default bins for continuous-valued histograms
_DEFAULT_BINS = 50

# BUG-575: a numeric column whose values have at most this many decimal places is
# recorded to a fixed precision (money, a rounded measurement); past it they carry a
# float's full digits and the precision pins nothing down.
_RECORDED_DECIMALS = 6

# BUG-564: rows of the smaller sample per merged bin when comparing a batch
# with the baseline on the baseline's edges -- see _aligned_kl.
_MIN_ROWS_PER_BIN = 20

# BUG-579: the normal upper-tail z of the 1e-6 significance every distribution check
# fires at (_chi2_crit). A finding that cannot reach it on the batch's rows abstains.
_SIG_Z = 4.753
_SIG_P = 1e-6

# BUG-575: a batch whose rank-sum statistic against the baseline is past this many
# standard errors is shifted. 5.0 is a two-sided tail of about 6e-7 per column; over
# 14,000 clean columns of the benchmark's shapes the largest |z| was 3.96.
_SHIFT_Z = 5.0
# ...and the shift must matter: a new value beats a baseline value at least 60% (or at
# most 40%) of the time. A batch of 100,000 rows makes any trivial wobble significant;
# a moved factor clears it (x2.5 on a heavy tail: 0.67 or more in the benchmark).
_SHIFT_EFFECT = 0.1
# BUG-579 re-review: while either sample has at most this many rows, the rank-sum
# test is a conservative exact one (_rank_sum_significant) instead of |z| > _SHIFT_Z.
# The normal cut cannot be reached below ~9 rows (|z| <= sqrt(3 n)), so a 5-row batch
# 3 sigma out was never proven, and on a few rows with heavy ties (a boolean, a
# small-integer count) its tail is not the normal's: a 3-row boolean baseline against
# 100 clean rows reached z = -5.7. With both samples past it the BUG-575 normal cut,
# calibrated on full batches, is kept.
_EXACT_RANK_ROWS = 64
# ...counted on a grid of about this many score groups times rows of the smaller
# sample while it has at most _EXACT_RANK_DP_ROWS rows, bounded by Chernoff beyond
# (see _rank_sum_significant).
_EXACT_RANK_GRID = 512
_EXACT_RANK_DP_ROWS = 16
# BUG-579 adversarial review: a 0/1 or small-int column (at most this many distinct
# values, each in a bin of its own) takes the exact tie-respecting rank test instead,
# on a score grid of about this many units (see _exact_tied_rank_p).
_EXACT_TIED_VALUES = 16
_EXACT_TIED_GRID = 1024

# BUG-568 data-quality thresholds -- see the "Data-quality drift" section.
_DQ_Z = 5.0                      # z-score a null-rate rise must reach
_NULL_MIN_RISE = 0.05            # ...and its minimum absolute rise
_VOLUME_FACTOR = 100             # row-count ratio that counts as a volume fault
_VOLUME_MIN_BASELINE_ROWS = 100  # too few baseline rows to judge volume below this


# BUG-573 column roles -- see _infer_role.
ROLE_IDENTIFIER, ROLE_DATE, ROLE_MEASURE = "identifier", "date", "measure"
_NO_DISTRIBUTION = frozenset({ROLE_IDENTIFIER, ROLE_DATE})
_ROLE_MIN_VALUES = 20       # fewer baseline values are no evidence of a role
_ID_COLLISION_EVIDENCE = 10.0  # expected equal pairs a measure would show (P(none) ~ 5e-5)
_SPARSE_ID_MIN_VALUES = 100  # unique ints on a sparse range need more values to count
_SPARSE_ID_BOUND = 4.0       # x the baseline's largest magnitude a keyed id may reach
_SEQUENCE_INSIDE = 0.10      # share of a batch inside its baseline's range no sequence holds
_MAX_FORMATS = 3            # more shapes than this is no stable format
_DATE_KINDS = frozenset({"datetime", "date", "Timestamp", "datetime64"})
_DECLARED_DATE = frozenset({"date", "datetime", "timestamp", "timestamptz", "datetime64", "datetime64[ns]"})
_DECLARED_NOT_ID = frozenset({"float", "double", "float32", "float64", "real", "bool", "boolean"})
# heal-acc1 review: a keyed id needs evidence beyond its values -- a declared key
# or an id-like declared type. BUG-576: never the column's name. A name hint
# (visit_num, total_number, seat_no) made a wide integer measure an identifier, and
# an identifier skips statistical drift, so x2 / x1.5 / in-range shifts went
# unchecked. A name may only ADD a check (_unique_by_name), never take one away.
_DECLARED_ID = frozenset({"id", "identifier", "key", "uuid", "guid", "serial", "bigserial", "smallserial"})
_ID_NAME_WORDS = frozenset({"id", "ids", "key", "pk", "fk", "uuid", "guid", "no", "nr", "num", "number"})
_NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _id_like_name(name: Any) -> bool:
    """The column's last name word names a key: customer_id, CustomerID, userId,
    order_no, account_key -- not population, bytes or revenue_cents."""
    words = _NAME_WORD.findall(str(name or ""))
    return bool(words) and words[-1].lower() in _ID_NAME_WORDS


def _is_complete_run(batch: BatchPayload) -> bool:
    """BUG-568 review: the batch declares it carries a whole run or period."""
    return (batch.metadata or {}).get("complete_run") is True


def _run_rows(batch: BatchPayload) -> int:
    """The row count of the run a complete-run batch closes.

    ADR-002 phase 2b: a pipeline pages a large load through /uasr/heal and only its
    last page declares ``complete_run``, so that page's own length is not the run's
    size; it declares the whole run's in ``metadata["run_rows"]``. A batch that
    declares nothing is the whole run itself.
    """
    n = (batch.metadata or {}).get("run_rows")
    if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
        return n
    return len(batch.rows)


def _beyond_range_tail(k: int, m: int, n0: int) -> float:
    """P(the ``k`` most extreme of the n0 + m values in a given split all belong to an
    ``m``-value batch) when batch and ``n0``-value baseline share one distribution
    (BUG-577): prod_{i<k} (m - i) / (n0 + m - i), whatever the distribution. Ties only
    make "strictly past" rarer, so it is an upper bound for discrete data."""
    p = 1.0
    for i in range(min(k, m)):
        p *= (m - i) / (n0 + m - i)
        if p < 1e-300:
            break
    return p if k <= m else 0.0


def _end_rows_p(below: int, above: int, m: int, n0: int) -> float:
    """The one exact past-range test (BUG-577, BUG-579): the p-value of ``below`` of an
    ``m``-row batch's values lying under an ``n0``-row baseline's min and ``above``
    over its max, when nothing changed.

    With k = below + above, "k rows outside the range" is the union over the k + 1
    splits (a lowest, k - a highest) of ``_beyond_range_tail(k, m, n0)``, so the union
    bound keeps the p-value valid. Merge of heal-realism and heal-hardening: realism
    took the smaller of the two one-sided tails, which is anti-conservative by up to
    2x (two looks, no correction); this bound never is, and a real fault (a whole
    column past the range) is still below 1e-20."""
    k = below + above
    return min(1.0, (k + 1) * _beyond_range_tail(k, m, n0))


def _chi2_crit(df: int) -> float:
    """The chi-square upper 1e-6 quantile (Wilson-Hilferty, z = _SIG_Z). It overstates
    the quantile at df=1 (27.5 vs 23.9), which errs toward not firing."""
    return df * (1 - 2 / (9 * df) + _SIG_Z * math.sqrt(2 / (9 * df))) ** 3


def _chi2_sf(x: float, df: int) -> float:
    """Upper tail of chi-square(df) at x, by the same Wilson-Hilferty normal
    approximation as _chi2_crit."""
    if x <= 0:
        return 1.0
    h = 2 / (9 * df)
    z = ((x / df) ** (1 / 3) - (1 - h)) / math.sqrt(h)
    return 0.5 * math.erfc(z / math.sqrt(2))


# ADR-002 "exact duplicates on the declared key", across the pages of one run: how many
# hashed keys the detector keeps, over every source's current run. 8 bytes per key plus
# a 4-byte page tag: 24 MB at the cap. A pipeline run pages 2,000 rows at a time
# (pipeline/heal.py), so the cap holds a 2-million-row run.
_RUN_KEYS_MAX = 2_000_000
_UINT64 = (1 << 64) - 1


_KEY_TEXT_INT = re.compile(r"[+-]?\d+")


def _key_part(value: Any) -> Any:
    """One declared-key cell as the key it names, or None for no key (PR #806 review 6).

    The rule, so that a key's type changing between pages neither hides nor invents a
    duplicate:
    * None and NaN carry no key value (the null-rate check judges them);
    * a number is its value: 1 and 1.0 are one key, 1.5 another;
    * text that reads back exactly as a number is that number: "1" is the key 1 and
      "2.5" the key 2.5, but "007", "+7" and "1.50" stay text -- padded codes are codes;
    * a boolean is a key of its own: True is not 1;
    * anything else is its text.
    """
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return ("bool", bool(value))
    if isinstance(value, (int, np.integer)):
        return ("num", int(value))
    if isinstance(value, (float, np.floating)):
        f = float(value)
        if math.isnan(f):
            return None
        return ("num", int(f)) if f.is_integer() and math.isfinite(f) else ("num", f)
    if isinstance(value, str):
        if _KEY_TEXT_INT.fullmatch(value) and str(int(value)) == value:
            return ("num", int(value))
        try:
            f = float(value)
        except ValueError:
            return ("text", value)
        if math.isfinite(f) and repr(f) == value:
            return ("num", int(f)) if f.is_integer() else ("num", f)
        return ("text", value)
    return ("text", str(value))


def _row_keys(rows: List[Dict[str, Any]], key: List[str]) -> List[str]:
    """Each row's declared-key value (normalised by ``_key_part``) as one string; rows
    with any part missing carry no key and are left out."""
    out = []
    for r in rows:
        parts = tuple(_key_part(r.get(c)) for c in key)
        if all(part is not None for part in parts):
            out.append(repr(parts))
    return out


def _key_hashes(keys: List[str]) -> np.ndarray:
    """The 64-bit hashes of a batch's declared-key values, unique and sorted. Python's
    string hash is salted per process, which is fine: the run memory is per process.
    Two keys of a 2-million-key run share a hash with probability ~1e-7, below the
    detector's 1e-6 false-alarm level."""
    return np.unique(np.fromiter((hash(k) & _UINT64 for k in keys), dtype=np.uint64, count=len(keys)))


class _RunKeys:
    """The declared-key values each source's current run has delivered so far.

    A run is the pages a caller sends under one ``metadata.run_id`` (pipeline/heal.py
    sends its run id on every page); the page that declares ``complete_run`` ends it.
    A key a page repeats from an earlier page of the same run is a duplicate as surely
    as one repeated inside a page.

    PR #806 review 4: a page's keys join the run when its rows are DELIVERED
    (``DriftDetector.record_delivered_keys``, after /uasr/heal decides), and only the
    rows it returns: a page that was quarantined and then re-sent corrected never
    delivered its keys. Checking (``seen``) never changes the memory -- a check under
    another run id used to drop the stored run. Each key keeps the page that first
    delivered it, so a page sent again (a retry: /uasr/heal derives a page's id from
    its run id and content when the caller sends none) is never its own duplicate.

    A new run id replaces the source's previous run, and so does any page after the
    one that ended the run (that page's own re-checks still see the run). Memory is
    bounded by ``_RUN_KEYS_MAX`` hashed keys over every source and tenant of the
    process: the least recently used runs are evicted first, whichever tenant they
    belong to, so one tenant's very large run can evict another tenant's older run,
    which then misses its cross-page repeats. A run that alone outgrows the cap keeps
    the keys it has, checks the rest against them, and stops adding (logged).
    """

    def __init__(self, max_keys: int = _RUN_KEYS_MAX) -> None:
        self.max_keys = max_keys
        self._lock = threading.Lock()
        # source -> [run_id, sorted hashes, page tag per hash, {batch_id: tag}, full, ended_by]
        self._runs: "OrderedDict[str, List[Any]]" = OrderedDict()
        self._total = 0

    def __len__(self) -> int:
        return self._total

    def _current(self, source_id: str, run_id: str, batch_id: str) -> Optional[List[Any]]:
        run = self._runs.get(source_id)
        if run is None or run[0] != run_id or run[5] not in (None, batch_id):
            return None
        return run

    def seen(self, source_id: str, run_id: str, batch_id: str, hashes: np.ndarray) -> int:
        """How many of ``hashes`` (unique, sorted) earlier pages of the run delivered.
        Read-only."""
        with self._lock:
            run = self._current(source_id, run_id, batch_id)
            if run is None:
                return 0
            hit, earlier = self._match(run, batch_id, hashes)
            return int(earlier.sum())

    def record(self, source_id: str, run_id: str, batch_id: str, hashes: np.ndarray, end: bool) -> None:
        """The delivered keys of a page join its run; ``end`` closes the run."""
        with self._lock:
            run = self._current(source_id, run_id, batch_id)
            if run is None:
                self._drop(source_id)
                run = self._runs[source_id] = [
                    run_id, np.empty(0, np.uint64), np.empty(0, np.int32), {}, False, None]
            self._runs.move_to_end(source_id)
            if end:
                run[5] = batch_id
                return
            hit, _ = self._match(run, batch_id, hashes)
            new = hashes[~hit]
            room = self.max_keys - self._total
            if len(new) > room:
                self._evict(keep=source_id, need=len(new) - room)
                room = self.max_keys - self._total
            if len(new) > room:
                if not run[4]:
                    logger.warning("run %s of %s outgrew %d remembered keys; later pages are "
                                   "checked against the first ones only", run_id, source_id, self.max_keys)
                run[4] = True
                new = new[:max(room, 0)]
            if len(new):
                pages = run[3]
                tag = pages.setdefault(batch_id, len(pages))
                at = np.searchsorted(run[1], new)
                run[1] = np.insert(run[1], at, new)
                run[2] = np.insert(run[2], at, np.int32(tag))
                self._total += len(new)

    @staticmethod
    def _match(run: List[Any], batch_id: str, hashes: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        stored, tags = run[1], run[2]
        tag = run[3].get(batch_id)
        pos = np.searchsorted(stored, hashes)
        inside = pos < len(stored)
        hit = np.zeros(len(hashes), dtype=bool)
        hit[inside] = stored[pos[inside]] == hashes[inside]
        earlier = hit.copy()
        if tag is not None:
            earlier[hit] = tags[pos[hit]] != tag
        return hit, earlier

    def forget(self, source_id: str) -> None:
        with self._lock:
            self._drop(source_id)

    def _drop(self, source_id: str) -> None:
        run = self._runs.pop(source_id, None)
        if run is not None:
            self._total -= len(run[1])

    def _evict(self, keep: str, need: int) -> None:
        for sid in list(self._runs):
            if need <= 0:
                return
            if sid != keep:
                need -= len(self._runs[sid][1])
                self._drop(sid)


class DriftDetector:
    """
    Stateful detector that compares incoming batches against stored baselines.

    Usage:
        detector = DriftDetector()
        detector.register_baseline("src_1", baseline_distributions)
        result = detector.detect(batch)
    """

    def __init__(
        self,
        default_zeta: float = 0.15,
        schema_strict: bool = True,
        semantic_threshold: float = 0.25,
        state_store: Optional[StateStore] = None,
        warmup_batches: int = 5,
        schema_intent_enabled: bool = False,
    ) -> None:
        # ζ — KL-divergence threshold.  Adapted dynamically per source.
        self._default_zeta = default_zeta
        self._schema_strict = schema_strict
        self._semantic_threshold = semantic_threshold
        # Opt-in (UASR_SCHEMA_INTENT_ENABLED): when on, a declared
        # schema-change intent (declare_schema_intent) that covers the
        # observed added/removed/retyped columns is adopted as the new
        # baseline instead of raising drift. Off by default -- unchanged
        # behaviour for every existing deployment.
        self._schema_intent_enabled = schema_intent_enabled
        # Cold-start suppression: the adaptive ζ needs ≥5 KL samples before
        # it reflects a source's real noise floor.  Until then, a 50-bin
        # histogram estimated off ~500 rows carries enough Poisson bin-noise
        # to push KL past the flat default ζ and fire false alarms.  For the
        # first ``warmup_batches`` numeric batches per source we still RECORD
        # the KL sample (so the window fills) but SUPPRESS the statistical
        # alert.  Set to 0 to disable and restore the original behaviour.
        self._warmup_batches = max(0, int(warmup_batches))

        # Per-source state (baselines, KL history, schema baselines,
        # reference embeddings) lives behind a pluggable StateStore so the
        # detector can scale horizontally.  The default is an unbounded
        # in-process store, which is BIT-IDENTICAL to the original four
        # loose dicts.  Pass ``InMemoryStateStore(capacity=N)`` to bound
        # memory with LRU eviction, or ``RedisStateStore(...)`` to share
        # state across worker replicas (resolves the cross-replica
        # cold-miss on Kafka partition rebalance).
        # NB: explicit ``is None`` — an empty InMemoryStateStore is falsy
        # (it defines __len__), so ``state_store or ...`` would discard a
        # caller-supplied empty store.
        self._store: StateStore = (
            state_store if state_store is not None else InMemoryStateStore()
        )

        # BUG-063/BUG-064: every load-mutate-save sequence below (register_
        # baseline, register_reference_embedding, declare_schema_intent,
        # detect) used to run with no lock across the round-trip, while
        # service.py's HTTP handlers dispatch these calls via
        # asyncio.to_thread -- real OS threads, not serialized by the event
        # loop. Two concurrent calls for the SAME source_id (a first-touch
        # race on a brand-new source, or two detect() calls on an existing
        # one) could each load a pre-update SourceState, mutate their own
        # copy, and save -- the second save silently clobbers the first,
        # losing a baseline/schema write or a KL-history sample. A per-
        # source threading.Lock (not asyncio.Lock: these calls run on
        # worker threads, not coroutines) serializes the round-trip per
        # source_id without serializing unrelated sources against each
        # other. The lock dict itself is guarded so two threads creating a
        # lock for the same brand-new source_id at once can't each get a
        # different Lock object.
        # BUG-578: an entry lives only while a thread holds or waits for its lock
        # ([lock, users], users counted under the guard before acquiring), so one
        # lock per source ever seen no longer accumulates, and a lock is never
        # dropped while a thread still waits on it.
        self._source_locks: Dict[str, List[Any]] = {}
        self._source_locks_guard = threading.Lock()
        # ADR-002 exact duplicates on the declared key across a run's pages (_RunKeys).
        self._run_keys = _RunKeys()

    @contextmanager
    def _lock_for(self, source_id: str) -> Iterator[None]:
        with self._source_locks_guard:
            entry = self._source_locks.get(source_id)
            if entry is None:
                entry = self._source_locks[source_id] = [threading.Lock(), 0]
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._source_locks_guard:
                entry[1] -= 1
                if entry[1] == 0:
                    del self._source_locks[source_id]

    # ────────────────────────────────────────────────────────────────
    # Back-compat read-only views
    # ------------------------------------------------------------------
    # Historically the detector exposed four public-ish dicts
    # (``_baselines`` etc.).  A handful of callers (health/list endpoints
    # in service.py, poisoning tests) read them.  We keep those working by
    # rebuilding read-only snapshots from the store.  Reads use ``peek`` so
    # they do NOT perturb LRU recency.  These are VIEWS — mutate state via
    # register_baseline / detect, not through these.
    # ────────────────────────────────────────────────────────────────
    @property
    def _baselines(self) -> Dict[str, Dict[str, ColumnDistribution]]:
        out: Dict[str, Dict[str, ColumnDistribution]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.baseline is not None:
                out[sid] = st.baseline
        return out

    @property
    def _schema_baselines(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.schema is not None:
                out[sid] = st.schema
        return out

    @property
    def _reference_embeddings(self) -> Dict[str, List[List[float]]]:
        out: Dict[str, List[List[float]]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.embeddings:
                out[sid] = st.embeddings
        return out

    @property
    def _kl_history(self) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.kl_history:
                out[sid] = st.kl_history
        return out

    # ────────────────────────────────────────────────────────────────
    # Baseline management
    # ────────────────────────────────────────────────────────────────

    def register_baseline(
        self,
        source_id: str,
        distributions_or_batch: "Dict[str, ColumnDistribution] | BatchPayload",
        schema: Optional[Dict[str, str]] = None,
    ) -> None:
        """Store a known-good distribution profile as the reference.

        Accepts either a pre-computed distribution dict or a raw
        ``BatchPayload`` (in which case distributions, schema and the
        reference embedding are computed automatically).
        """
        baseline_rows: Optional[Dict[str, Any]] = None
        if isinstance(distributions_or_batch, BatchPayload):
            batch = distributions_or_batch
            distributions = self._compute_distributions(batch)
            if not schema:
                schema = batch.schema_snapshot or (
                    {c: "unknown" for c in batch.columns} if batch.columns else None
                )
            # BUG-573: roles first -- the reference embedding must leave out the same
            # identifier and date columns every later batch's embedding leaves out.
            declared = batch.schema_snapshot or schema or {}
            declared_key = set((batch.metadata or {}).get("key_columns") or [])
            unique_by_name = set()
            for col, dist in distributions.items():
                values = [r.get(col) for r in batch.rows]
                dist.role, dist.value_format, dist.value_bound = self._infer_role(
                    values, dist, declared.get(col), keyed=col in declared_key)
                dist.sequence_span = self._sequence_span(values, dist)
                if self._unique_by_name(values, dist, col):
                    unique_by_name.add(col)
            # Also register a reference embedding so semantic drift works
            emb = self._compute_batch_embedding(batch, exclude=self._no_distribution_columns(distributions))
            if emb:
                self.register_reference_embedding(source_id, emb)
            if batch.rows:
                cols = batch.columns or list(batch.rows[0].keys())
                key = [c for c in ((batch.metadata or {}).get("key_columns") or []) if c in cols]
                dedupe = key or list(cols)
                pairs, triples = self._equal_row_tuples(batch, dedupe)
                baseline_rows = {
                    "rows": len(batch.rows),
                    "columns": list(cols),
                    # BUG-574: ADR-002 judges duplicates on the declared key, else on
                    # the full row.
                    "key_columns": key,
                    "equal_pairs": pairs,
                    "equal_triples": triples,
                    # BUG-574: an identifier column (BUG-573) makes every row unique
                    # by construction, so with no duplicate in the baseline any
                    # duplicate in a batch is a fault. A measure that merely happened
                    # to be distinct across the baseline (a coarse float) does not
                    # count: at ten times the rows it repeats by chance, and ADR-002
                    # allows no false alarm on clean data.
                    "unique_columns": [
                        c for c in cols
                        if (d := distributions.get(c)) is not None
                        and (d.role == ROLE_IDENTIFIER or c in unique_by_name)
                    ],
                    # BUG-568 review: only a baseline that is itself a whole
                    # run can anchor the volume check -- see _check_volume_drift.
                    "complete_run": _is_complete_run(batch),
                    # ADR-002 phase 2b: the run's size, which a paged baseline's
                    # ``rows`` (the page it was registered from) is not.
                    "run_rows": _run_rows(batch),
                }
        else:
            distributions = distributions_or_batch

        with self._lock_for(source_id):
            st = self._store.load(source_id)
            # BUG-573: a distributions-only re-baseline (mapek_worker) carries no rows
            # to infer roles from; a column keeps the role it was given, so a sequential
            # id does not turn back into a measure that drifts on every batch.
            for col, dist in distributions.items():
                prev = (st.baseline or {}).get(col)
                if dist.role is None and prev is not None and prev.role and prev.value_kind == dist.value_kind:
                    dist.role, dist.value_format, dist.value_bound, dist.sequence_span = (
                        prev.role, prev.value_format, prev.value_bound, prev.sequence_span)
            st.baseline = distributions
            # BUG-568: a distributions-only re-baseline (mapek_worker) carries
            # no row-level facts, so the previous batch's duplicate profile is
            # dropped rather than compared against a baseline it no longer
            # describes (e.g. a column removed since makes rows less unique).
            st.baseline_rows = baseline_rows
            if schema:
                st.schema = schema
            self._store.save(source_id, st)
        logger.info("Registered baseline for source=%s (%d columns)", source_id, len(distributions))

    def baseline_distance(self, source_id: str, batch: "BatchPayload") -> float:
        """DSR-007b: a single continuous drift-distance score for a batch,
        independent of which detector (classical or martingale) actually
        fired.

        Mean absolute standardized distance
        ``|batch_mean - baseline_mean| / max(baseline_std, eps)`` across
        every numeric column that has a registered baseline with a nonzero
        sample count. Returns 0.0 when no baseline is registered yet, or
        when no column overlaps -- callers get a real number always, never
        None, so it can feed a canary outcome-history record unconditionally.

        Deliberately reuses the summary-stat ``ColumnDistribution`` already
        stored by ``register_baseline`` rather than retaining raw samples
        (which the martingale channel does, at real per-source memory cost)
        -- this is a coarser signal, but "coarse and always available" is
        what DSR-007a/b need for a canary outcome, not a statistical test.
        """
        baseline = self._store.peek(source_id).baseline
        if not baseline:
            return 0.0
        col_samples: Dict[str, List[float]] = {}
        for col in batch.columns:
            samples = [
                float(v) for r in batch.rows
                if isinstance((v := r.get(col)), (int, float)) and not isinstance(v, bool)
            ]
            if samples:
                col_samples[col] = samples
        distances: List[float] = []
        for col, samples in col_samples.items():
            dist = baseline.get(col)
            if dist is None or dist.mean is None or dist.std is None:
                continue
            batch_mean = sum(samples) / len(samples)
            std = max(dist.std, 1e-9)
            distances.append(abs(batch_mean - dist.mean) / std)
        return sum(distances) / len(distances) if distances else 0.0

    def has_baseline(self, source_id: str) -> bool:
        """Whether a baseline has ever been registered for this source."""
        return bool(self._store.peek(source_id).baseline)

    def schema_baseline(self, source_id: str) -> Optional[Dict[str, str]]:
        """The schema this source's batches are compared against, if any.

        BUG-563: the first batch a source ever sends is adopted as its schema baseline
        even without a registered distribution baseline (_check_schema_drift), so a
        caller that registers one "only if absent" must keep that schema, not replace
        it with the batch it is registering.
        """
        return self._store.peek(source_id).schema

    def baseline_distributions(self, source_id: str) -> Dict[str, ColumnDistribution]:
        """The per-column baseline profile, the evidence ADR-002's proven repairs check against."""
        return dict(self._store.peek(source_id).baseline or {})

    def register_reference_embedding(self, source_id: str, embedding: List[float]) -> None:
        """Add a reference embedding vector to the source's context matrix."""
        with self._lock_for(source_id):
            st = self._store.load(source_id)
            st.embeddings.append(embedding)
            self._store.save(source_id, st)

    def declare_schema_intent(
        self,
        source_id: str,
        added: Optional[Dict[str, str]] = None,
        removed: Optional[List[str]] = None,
        type_changes: Optional[Dict[str, str]] = None,
        ttl_seconds: float = 3600.0,
        note: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> None:
        """Declare a sanctioned upcoming schema change for a source.

        Once declared, ``_check_schema_drift`` treats an observed
        added/removed/retyped-column diff as non-drift -- adopting it as
        the new baseline instead -- as long as the diff is fully covered by
        what was declared here and the intent hasn't expired. One active
        intent per source; a new declaration overwrites the previous one.

        No-ops when the feature is off (``schema_intent_enabled=False``) --
        the HTTP boundary (POST /uasr/schema-intent) is expected to gate
        first and fail closed (400), but this keeps the detector itself
        safe to call directly (e.g. from tests) without the flag.
        """
        if not self._schema_intent_enabled:
            return
        with self._lock_for(source_id):
            st = self._store.load(source_id)
            st.schema_intent = {
                "added": dict(added or {}),
                "removed": list(removed or []),
                "type_changes": dict(type_changes or {}),
                "expires_at": time.time() + float(ttl_seconds),
                "note": note,
                "actor": actor,
            }
            self._store.save(source_id, st)
        logger.info(
            "Declared schema intent for source=%s added=%s removed=%s type_changes=%s "
            "ttl_seconds=%.0f actor=%s note=%s",
            source_id,
            list(st.schema_intent["added"]),
            st.schema_intent["removed"],
            list(st.schema_intent["type_changes"]),
            ttl_seconds,
            actor,
            note,
        )

    # ────────────────────────────────────────────────────────────────
    # Main detection
    # ────────────────────────────────────────────────────────────────

    def detect(
        self, batch: BatchPayload, *, record_state: bool = True
    ) -> DriftDetectionResult:
        """Run all drift checks on an incoming batch.

        ``record_state=False`` runs the checks against a disposable copy of
        the source's state and never persists it, so a validation-only call
        (RecoveryLoop re-checking a candidate shim) cannot corrupt
        ``kl_history`` / the adaptive ζ that the live Monitor loop depends on
        for every future event on that source.
        """
        result = DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
        )

        # Load-mutate-save the source's state under its per-source lock
        # (BUG-064) so a concurrently-arriving batch for the SAME source_id
        # (another HTTP request, or the mapek_worker Kafka loop, both
        # dispatched via asyncio.to_thread onto real OS threads) can't
        # interleave with this round-trip and silently lose a KL-history
        # sample or schema-baseline update. Single store round-trip, single
        # LRU touch, threaded through the checks, persisted once at the
        # end.  ``dirty`` tracks whether any check mutated the state
        # (first-seen schema baseline, appended KL sample) so we avoid a
        # redundant write on read-only batches.
        with self._lock_for(batch.source_id):
            st = self._store.load(batch.source_id)
            if not record_state:
                # kl_history is mutated in place (append/trim) by
                # _check_statistical_drift, so a shallow dataclass copy would
                # still share (and corrupt) the same list — copy it explicitly.
                st = replace(st, kl_history=list(st.kl_history))
            dirty = False

            # BUG-568: every check runs and every finding is reported. The
            # first-hit fields (drift_type, severity, drift_vector, details,
            # kl_divergence, cosine_distance) keep the old precedence --
            # schema, statistical, semantic -- so no existing caller sees a
            # different primary drift; the data-quality checks only become
            # primary when none of those fired.
            findings: List[Tuple[DriftType, Dict[str, Any]]] = []

            # 1. Schema drift
            schema_drift, d1 = self._check_schema_drift(batch, st)
            dirty = dirty or d1
            if schema_drift:
                findings.append((DriftType.SCHEMA, schema_drift))

            # 2. Statistical drift (KL-Divergence). After a schema hit it runs
            # on a copy of kl_history: the old early return never recorded a
            # KL sample for a schema-drifted batch, and the adaptive zeta
            # must keep learning only from batches it would have learned from.
            stat_st = replace(st, kl_history=list(st.kl_history)) if schema_drift else st
            stat_drift, d2 = self._check_statistical_drift(batch, stat_st)
            if not schema_drift:
                dirty = dirty or d2
            if stat_drift:
                findings.append((DriftType.STATISTICAL, stat_drift))

            # 3. Semantic drift (embedding distance) — only if embeddings registered
            sem_drift = self._check_semantic_drift(batch, st)
            if sem_drift:
                findings.append((DriftType.SEMANTIC, sem_drift))

            # 4-6. Data-quality drift against the baseline (BUG-568).
            for dq_type, dq_check in (
                (DriftType.NULL_RATE, self._check_null_rate_drift),
                (DriftType.VOLUME, self._check_volume_drift),
            ):
                dq_drift = dq_check(batch, st)
                if dq_drift:
                    findings.append((dq_type, dq_drift))
            dup_drift = self._check_duplicate_drift(batch, st)
            if dup_drift:
                findings.append((DriftType.DUPLICATE, dup_drift))

            if dirty and record_state:
                self._store.save(batch.source_id, st)

        if not findings:
            result.details = "No drift detected"
            return result

        primary_type, primary = findings[0]
        result.drift_detected = True
        result.drift_type = primary_type
        result.severity = primary["severity"]
        result.affected_columns = primary.get("affected_columns", [])
        result.drift_vector = primary
        result.details = primary.get("details", "")
        if primary_type == DriftType.STATISTICAL:
            result.kl_divergence = primary["max_kl"]
        elif primary_type == DriftType.SEMANTIC:
            result.cosine_distance = primary["cosine_distance"]
        result.drift_types = [t for t, _ in findings]
        result.drift_vectors = {t.value: v for t, v in findings}
        return result

    # ────────────────────────────────────────────────────────────────
    # Schema drift
    # ────────────────────────────────────────────────────────────────

    def _check_schema_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Returns ``(drift_or_None, state_was_mutated)``."""
        baseline_schema = st.schema
        if not baseline_schema:
            # First time — treat batch schema as baseline
            if batch.schema_snapshot:
                st.schema = batch.schema_snapshot
                return None, True
            elif batch.columns:
                st.schema = {c: "unknown" for c in batch.columns}
                return None, True
            return None, False

        current_cols = set(batch.columns) if batch.columns else set()
        if batch.schema_snapshot:
            current_cols = set(batch.schema_snapshot.keys())

        baseline_cols = set(baseline_schema.keys())

        added = current_cols - baseline_cols
        removed = baseline_cols - current_cols

        if not added and not removed:
            # column -> (baseline type, batch type)
            changed: Dict[str, Tuple[str, str]] = {}
            if batch.schema_snapshot:
                for col, dtype in batch.schema_snapshot.items():
                    if col in baseline_schema and not schema_types_match(baseline_schema[col], dtype):
                        changed[col] = (baseline_schema[col], dtype)
            # BUG-572: the declared types are not the only evidence. Without a
            # snapshot (or with one that says only "number") an int column whose
            # values now arrive as 5.0 matched every check above.
            kind_changes = self._value_kind_changes(batch, st.baseline, baseline_schema)
            for col, kinds in kind_changes.items():
                changed.setdefault(col, kinds)
            if changed:
                type_changes = list(changed)
                type_changed_types = {c: new for c, (_, new) in changed.items()}
                if self._schema_intent_covers(st, {}, set(), type_changed_types):
                    self._adopt_schema_from_intent(batch, st)
                    # The sanctioned kind becomes the baseline's, or the change
                    # alarms again the moment the intent expires. A new dict:
                    # under record_state=False st.baseline is shared with the store.
                    if kind_changes and st.baseline:
                        st.baseline = {
                            **st.baseline,
                            **{c: st.baseline[c].model_copy(update={"value_kind": new})
                               for c, (_, new) in kind_changes.items()},
                        }
                    return None, True
                return {
                    "type": "type_change",
                    "affected_columns": type_changes,
                    "severity": DriftSeverity.MEDIUM,
                    "details": f"Type changed in columns: {type_changes}",
                    "old_types": {c: old for c, (old, _) in changed.items()},
                    "new_types": type_changed_types,
                }, False
            # BUG-573: an identifier or a date has no distribution to drift, but its
            # format can break (ORD-0001234 arriving as 1234, 2024-01-05 as 01/05/2024)
            # -- a contract change, not a value to repair, so no rule restores it.
            formats = self._format_changes(batch, st.baseline)
            if formats:
                return {
                    "type": "format_change",
                    "affected_columns": sorted(formats),
                    "severity": DriftSeverity.MEDIUM,
                    "details": "Value format changed in columns: " + ", ".join(
                        f"{c} {f['baseline_formats']} -> {f['new_formats']}" if "new_formats" in f
                        else f"{c} has {f['values_over_bound']} value(s) past {f['value_bound']:g} "
                             f"(e.g. {f['example']!r})"
                        for c, f in sorted(formats.items())),
                    "formats": formats,
                }, False
            if batch.schema_snapshot:
                # A column the baseline only ever saw as null gets its type from the
                # first batch that carries a value, so later retypes are detectable.
                learned = {
                    c: t for c, t in batch.schema_snapshot.items()
                    if baseline_schema.get(c) == "unknown" and t != "unknown"
                }
                if learned:
                    st.schema = {**baseline_schema, **learned}
                    return None, True
            return None, False

        added_types = {
            c: (batch.schema_snapshot.get(c) if batch.schema_snapshot else None) for c in added
        }
        if self._schema_intent_covers(st, added_types, removed, {}):
            self._adopt_schema_from_intent(batch, st)
            return None, True

        severity = DriftSeverity.HIGH if removed else DriftSeverity.MEDIUM
        if len(removed) > len(baseline_cols) * 0.5:
            severity = DriftSeverity.CRITICAL

        return {
            "type": "schema_change",
            "added": list(added),
            "removed": list(removed),
            "affected_columns": list(added | removed),
            "severity": severity,
            "details": f"Added: {list(added)}, Removed: {list(removed)}",
        }, False

    @staticmethod
    def _value_kind(values: List[Any]) -> Optional[str]:
        """The kind of a column's non-null values, from the values themselves (BUG-572).

        None and NaN are missing values and are skipped.
        A mix of ints and floats is "float": a float column legitimately carries
        whole numbers, and a JSON client may send them as 5. Any other mix is
        "mixed", which has no stable kind and is never compared. numpy scalars
        (rows from a DataFrame) count as the Python kind they stand for.
        """
        kinds = set()
        for v in values:
            # NaN is how pandas writes a missing value, in an int column too; it
            # says nothing about the column's kind, any more than None does.
            if v is None or (isinstance(v, (float, np.floating)) and v != v):
                continue
            if isinstance(v, (bool, np.bool_)):
                kinds.add("bool")
            elif isinstance(v, (int, np.integer)):
                kinds.add("int")
            elif isinstance(v, (float, np.floating)):
                kinds.add("float")
            elif isinstance(v, str):
                kinds.add("str")
            else:
                kinds.add(type(v).__name__)
        if not kinds:
            return None
        if kinds == {"int", "float"}:
            return "float"
        return kinds.pop() if len(kinds) == 1 else "mixed"

    @staticmethod
    def _decimal_places(value: Any) -> int:
        """Decimal places of a number in its shortest written form (170.3 -> 1, 5 -> 0)."""
        if isinstance(value, (bool, np.bool_)):
            return 0
        if isinstance(value, (int, np.integer)):
            return 0
        f = float(value)
        if not math.isfinite(f):
            return 0
        return max(0, -int(Decimal(repr(f)).as_tuple().exponent))

    @classmethod
    def _recorded_decimals(cls, distinct: np.ndarray) -> Optional[int]:
        """The precision a numeric column is recorded to (BUG-575), or None.

        The most decimal places of any of its distinct values; None past
        ``_RECORDED_DECIMALS``, where the values carry a float's full digits and no
        fixed precision. Whole values have none, so only the others are written out.
        """
        most = 0
        for v in distinct[distinct != np.floor(distinct)].tolist():
            places = cls._decimal_places(v)
            if places > _RECORDED_DECIMALS:
                return None
            most = max(most, places)
        return most

    # Declared snapshot type names for a float column ("number" and "unknown" are
    # not: they leave the recorded kind standing).
    _DECLARED_FLOAT = frozenset({"float", "double", "float32", "float64", "real"})

    def _value_kind_changes(
        self,
        batch: BatchPayload,
        baseline: Optional[Dict[str, ColumnDistribution]],
        baseline_schema: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Tuple[str, str]]:
        """Columns whose value kind differs from the baseline's kind.

        Kinds are compared, never values: a float column whose baseline happened to
        hold only whole numbers was still recorded as "float" (its values were 5.0),
        so a batch of whole-number floats matches it. The one tolerated difference
        is int values in a float column -- lossless, and how JSON writes 5.0.

        The baseline's kind is the declared one where the baseline schema names a
        kind its values fit: a declared float column whose baseline arrived as JSON
        ints (10.0 written as 10) was recorded as "int", but it is a float column,
        and its fractional values are not a retype. Where the declaration does not
        fit the baseline's own values (declared int, sent as 5.0) the values are
        what the source really sends, and the recorded kind stands.
        """
        if not baseline or not batch.rows:
            return {}
        out: Dict[str, Tuple[str, str]] = {}
        for col, dist in baseline.items():
            old = dist.value_kind
            if old is None or old == "mixed":
                continue
            declared = str((baseline_schema or {}).get(col, "")).lower()
            if old == "int" and declared in self._DECLARED_FLOAT:
                old = "float"
            new = self._value_kind([r.get(col) for r in batch.rows])
            if new is None or new == old or (old == "float" and new == "int"):
                continue
            out[col] = (old, new)
        return out

    # ────────────────────────────────────────────────────────────────
    # Column roles (BUG-573)
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _shape(text: str, collapse: bool) -> str:
        """A value's format: digits as 9, letters as A, everything else as itself.

        ``collapse`` merges runs (identifiers: ORD-12 and ORD-0001234 are both
        "A-9"); dates keep every position, since 2024-01-05 and 01-05-2024 differ
        only in where the digits sit.
        """
        out: List[str] = []
        for ch in text:
            c = "9" if ch.isdigit() else "A" if ch.isalpha() else ch
            if collapse and out and out[-1] == c and c in "9A":
                continue
            out.append(c)
        return "".join(out)

    @staticmethod
    def _is_iso_date(text: str) -> bool:
        # A separator is required: fromisoformat also reads bare digit strings
        # (20240105), which are as likely to be codes as dates.
        if not any(sep in text for sep in "-:/T"):
            return False
        try:
            _dt.datetime.fromisoformat(text)
            return True
        except ValueError:
            return False

    @classmethod
    def _formats(cls, values: List[str], collapse: bool) -> Optional[List[str]]:
        shapes = sorted({cls._shape(v, collapse) for v in values})
        return shapes if len(shapes) <= _MAX_FORMATS else None

    @staticmethod
    def _expected_int_collisions(ints: List[int]) -> float:
        """Equal pairs ``len(ints)`` independent draws from a smooth density shaped
        like these values would show: C(n,2) * sum of p_bin^2 / bin width, on the
        values' own grid (their gcd, so ids stepping by 10 are counted per step).

        Averaging the density over bins can only lower the sum (Jensen), so this
        understates the collisions a measure would show: a column called an
        identifier on it is unique beyond what chance gives a measurement.
        """
        n = len(ints)
        lo = min(ints)
        try:
            offsets = np.asarray([v - lo for v in ints], dtype=np.int64)
        except OverflowError:
            return 0.0
        grid = int(np.gcd.reduce(offsets)) or 1
        units = offsets // grid
        span = int(units.max()) + 1
        nbins = int(max(1, min(_DEFAULT_BINS, n // 5, span)))
        idx = np.minimum((units.astype(float) * nbins / span).astype(np.int64), nbins - 1)
        counts = np.bincount(idx, minlength=nbins).astype(float)
        widths = np.full(nbins, span / nbins)
        sum_p2 = float(np.sum(counts * (counts - 1) / widths)) / (n * (n - 1))
        return n * (n - 1) / 2 * sum_p2

    def _infer_role(
        self, values: List[Any], dist: ColumnDistribution, declared: Any = None,
        keyed: bool = False,
    ) -> Tuple[str, Optional[List[str]], Optional[float]]:
        """``(role, value_format, value_bound)`` of a column, from its baseline values
        and declared type (BUG-573).

        * date -- datetime values, or text that is all ISO dates/timestamps;
        * identifier -- no nulls, at least 20 values, every value distinct, and
          - text: a stable format with digits in it (at most three shapes;
            ORD-0001234 is "A-9"), which free text such as names does not have;
          - integers: uniqueness no measurement has by chance -- a smooth density
            over the same range would have shown >= 10 equal pairs (a sequence);
          - or integers unique over >= 100 values on a sparse range (a keyed id).
            Values alone cannot tell that from a wide integer measure (population,
            byte counts, cents), which would then go unchecked but for a 4x bound,
            so it also needs a declaration -- the column is in the batch's declared
            ``key_columns``, or its declared type names a key (``id``, ``uuid``,
            ``serial``...) -- or the order-free shape of a scrambled counter
            (``_lattice``). Its name is no evidence (BUG-576), and neither is its row
            order (PR #806 review: a 99% monotone test turned a sparse id with 2% of
            its rows out of order into a measure, and every clean batch alarmed).
            Undeclared, the column stays a measure and keeps its statistical checks,
            since checking a real id statistically costs nothing when its
            population is stable, and skipping a real measure lets a x2 through.
            A growing one is a sequence candidate (``_sequence_span``): a batch that
            continues past the baseline is judged as a sequence instead
            (``_continues_sequence``). A declared keyed id gets ``value_bound``: a
            value past 4x the baseline's largest magnitude is a format change.
          Floats and booleans are never identifiers.
        * measure -- everything else: compared statistically, as before.
        """
        present = [v for v in values if v is not None and not (isinstance(v, float) and v != v)]
        kind = dist.value_kind
        decl = str(declared or "").strip().lower()
        if not present:
            return ROLE_MEASURE, None, None
        if kind in _DATE_KINDS:
            return ROLE_DATE, None, None
        if kind == "str" and all(self._is_iso_date(v) for v in present):
            if len(present) >= _ROLE_MIN_VALUES or decl in _DECLARED_DATE:
                return ROLE_DATE, self._formats(present, collapse=False), None
        if (
            kind not in ("str", "int")
            or len(present) < _ROLE_MIN_VALUES or len(present) != len(values)
            or decl in _DECLARED_NOT_ID or len(set(present)) != len(present)
        ):
            return ROLE_MEASURE, None, None
        if kind == "str":
            fmt = self._formats(present, collapse=True)
            if fmt is not None and all("9" in f for f in fmt):
                return ROLE_IDENTIFIER, fmt, None
            return ROLE_MEASURE, None, None
        ints = [int(v) for v in present]
        if self._expected_int_collisions(ints) >= _ID_COLLISION_EVIDENCE:
            return ROLE_IDENTIFIER, None, None
        if len(ints) >= _SPARSE_ID_MIN_VALUES and (keyed or decl in _DECLARED_ID or self._lattice(ints)):
            return ROLE_IDENTIFIER, None, float(_SPARSE_ID_BOUND * max(abs(v) for v in ints))
        return ROLE_MEASURE, None, None

    @staticmethod
    def _lattice(ints: List[int]) -> bool:
        """A counter scrambled by an affine map mod a prime (k * stride mod p), an
        obfuscated id over a fixed population, by its value set alone: sorted, such a
        set steps by at most three sizes (the three-gap theorem); >= 90% of the steps
        here, to allow a few deleted rows. A measure's sorted values on a sparse range
        step by many sizes, and so does a gapped auto-increment.

        Order-free (PR #806 review): the same values in any row order give the same
        answer. It must stay an identifier: its batches are runs of consecutive
        counters, not a random sample of its population, so compared as a measure its
        clean batches drift (6 of 1,182 clean synthetic batches alarmed without it)."""
        steps = np.diff(np.sort(np.asarray(ints, dtype=np.int64)))
        if len(steps) == 0:
            return False
        _, counts = np.unique(steps, return_counts=True)
        return int(np.sort(counts)[-3:].sum()) >= 0.9 * len(steps)

    def _sequence_span(self, values: List[Any], dist: ColumnDistribution) -> Optional[List[float]]:
        """The baseline's ``[median, max, median step]`` for a sequence candidate: an int
        measure, null-free and unique over >= 100 values on a sparse range -- what an
        undeclared auto-increment with gaps looks like, whatever order its rows came
        in (PR #806 review). The step is the median gap between consecutive sorted
        values. None for every other column.

        A candidate keeps every measure check. Only a batch that continues past the
        baseline (``_continues_sequence``) is judged as a sequence: its rows past the
        range are the next ids, not a shift, and a unit change is a format change
        (``_format_changes``)."""
        if dist.role != ROLE_MEASURE or dist.value_kind != "int":
            return None
        ints = [v for v in values if v is not None]
        if (len(ints) < _SPARSE_ID_MIN_VALUES or len(ints) != len(values)
                or len(set(ints)) != len(ints) or any(isinstance(v, bool) for v in ints)):
            return None
        ints = sorted(int(v) for v in ints)
        return [float(np.median(ints)), float(ints[-1]), float(np.median(np.diff(ints)))]

    @staticmethod
    def _continues_sequence(dist: ColumnDistribution, values: List[Any]) -> bool:
        """Whether a sequence candidate's batch values continue past its baseline (PR
        #806 review): fewer than max(2, 10%) of them lie inside the baseline's range.

        Order-free: it reads the values, not the order the rows came in. A re-sent
        old id or two (an upsert, a CDC update) does not stop a batch continuing --
        "any value back below the median" used to, and quarantined every row. A
        measure's batch from the same distribution lies inside the range, and a x2,
        x1.5 or in-range fault leaves >= 10% of a wide measure inside, so those are
        still checked as a measure; a x10 or more lands past the 4x bound instead."""
        span = dist.sequence_span
        if not span:
            return False
        top = span[1]
        nums = [v for v in values
                if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))]
        if not nums:
            return False
        inside = sum(1 for v in nums if v <= top)
        return inside < max(2.0, _SEQUENCE_INSIDE * len(nums))

    @staticmethod
    def _sequence_unit_change(dist: ColumnDistribution, values: List[Any]) -> Optional[Dict[str, Any]]:
        """A continuing sequence's batch that changed unit: values past 4x the baseline's
        largest id AND steps between them 4x the baseline's median step (PR #806
        review). The step test is what tells a unit change from growth: a sequence that
        has grown past 4x its baseline still steps as it did (a fixed 4x bound alone
        alarmed on every batch after that), while x1000 multiplies every step by 1000.
        It needs >= 3 values for a median step; a batch of one or two ids cannot tell."""
        span = dist.sequence_span or []
        bound = _SPARSE_ID_BOUND * abs(span[1])
        nums = sorted(float(v) for v in values
                      if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_)))
        over = [v for v in nums if abs(v) > bound]
        if not over:
            return None
        if len(span) > 2:
            if len(nums) < 3 or float(np.median(np.diff(nums))) <= _SPARSE_ID_BOUND * max(span[2], 1.0):
                return None
        return {"value_bound": bound, "values_over_bound": len(over), "example": over[0]}

    @staticmethod
    def _column_values(batch: BatchPayload, col: str) -> List[Any]:
        return [v for r in batch.rows if (v := r.get(col)) is not None]

    @staticmethod
    def _unique_by_name(values: List[Any], dist: ColumnDistribution, name: Any) -> bool:
        """BUG-576: the one thing a column's name may do -- add a check. An int column,
        null-free and unique over >= 100 baseline values, whose name names a key
        (customer_id, order_no) stays a measure with every statistical check, and
        also makes the baseline's rows unique by construction (BUG-574), so a
        duplicated row in a batch is an exact alert as for a declared key. Were it a
        measure unique only by chance, a batch would repeat a whole row by chance
        only if every other column repeated with it."""
        present = [v for v in values if v is not None]
        return (
            dist.value_kind == "int" and len(present) == len(values) >= _SPARSE_ID_MIN_VALUES
            and len(set(present)) == len(present) and _id_like_name(name)
        )

    @staticmethod
    def _no_distribution_columns(baseline: Optional[Dict[str, ColumnDistribution]]) -> set:
        return {c for c, d in (baseline or {}).items() if d.role in _NO_DISTRIBUTION}

    def _format_changes(
        self, batch: BatchPayload, baseline: Optional[Dict[str, ColumnDistribution]],
    ) -> Dict[str, Dict[str, Any]]:
        """Identifier and date columns holding a value of a shape their baseline never
        had, or a keyed id's value past its ``value_bound``."""
        out: Dict[str, Dict[str, Any]] = {}
        for col, dist in (baseline or {}).items():
            if dist.sequence_span and dist.role not in _NO_DISTRIBUTION:
                # PR #806 review: a sequence candidate whose batch continues past the
                # baseline skips the measure checks, so a unit change must show here:
                # x1000 on an undeclared order_id was silent.
                values = self._column_values(batch, col)
                if self._continues_sequence(dist, values) and (found := self._sequence_unit_change(dist, values)):
                    out[col] = found
                continue
            if dist.role not in _NO_DISTRIBUTION:
                continue
            if dist.value_bound is not None:
                over = [v for r in batch.rows
                        if isinstance(v := r.get(col), (int, float, np.integer, np.floating))
                        and not isinstance(v, (bool, np.bool_)) and abs(v) > dist.value_bound]
                if over:
                    out[col] = {"value_bound": dist.value_bound, "values_over_bound": len(over),
                                "example": over[0]}
                continue
            if not dist.value_format:
                continue
            allowed = set(dist.value_format)
            collapse = dist.role == ROLE_IDENTIFIER
            new = sorted({
                self._shape(v, collapse) for r in batch.rows
                if isinstance(v := r.get(col), str)
            } - allowed)
            if new:
                out[col] = {"baseline_formats": list(dist.value_format), "new_formats": new[:5]}
        return out

    def _schema_intent_covers(
        self,
        st: SourceState,
        added: Dict[str, Optional[str]],
        removed: set,
        type_changed: Dict[str, str],
    ) -> bool:
        """True if a live, unexpired declared intent covers this exact diff.

        Coverage is a subset check per change kind -- an undeclared extra
        column change (an addition on top of a declared one, say) falls
        through to the ordinary drift path unchanged, which is the
        guarantee that an intent only ever suppresses the specific change
        it named.

        ``added`` and ``type_changed`` map column -> the batch's *observed*
        new type (``None`` when the batch carried no ``schema_snapshot`` and
        the type genuinely can't be known). Matching by column name alone
        would let a declared intent for e.g. "a: int -> float" silently
        absorb ANY subsequent type on "a" (say a corrupt "a: int -> string")
        for the rest of the TTL window -- so when the observed type is
        known, it must equal the declared target type, not just share the
        column name.
        """
        if not self._schema_intent_enabled:
            return False
        intent = st.schema_intent
        if not intent:
            return False
        if intent.get("expires_at", 0.0) <= time.time():
            return False
        declared_added = dict(intent.get("added") or {})
        declared_removed = set(intent.get("removed") or [])
        declared_type_changes = dict(intent.get("type_changes") or {})

        if not removed <= declared_removed:
            return False
        for col, observed_type in added.items():
            if col not in declared_added:
                return False
            if observed_type is not None and observed_type != declared_added[col]:
                return False
        for col, observed_type in type_changed.items():
            if col not in declared_type_changes:
                return False
            if observed_type is not None and observed_type != declared_type_changes[col]:
                return False
        return True

    @staticmethod
    def _adopt_schema_from_intent(batch: BatchPayload, st: SourceState) -> None:
        """Adopt the batch's schema as the new baseline (intent-covered change).

        Mirrors the first-time-seen assignment in ``_check_schema_drift``.
        Clears the intent once the schema has fully reached the declared
        target -- every declared add present, no declared removal still
        present, AND every declared type change actually landed at its
        target type -- so a multi-batch rollout across app instances (only
        some columns changed this batch) keeps being tolerated until then,
        or until TTL expiry. Checking only added/removed here let a
        multi-column type-change intent get cleared after just the first
        column's type changed, spuriously flagging the still-pending
        declared columns as drift once the intent was already gone.
        """
        if batch.schema_snapshot:
            st.schema = dict(batch.schema_snapshot)
        elif batch.columns:
            st.schema = {c: "unknown" for c in batch.columns}

        intent = st.schema_intent
        if intent is None:
            return
        declared_added = set(intent.get("added") or {})
        declared_removed = set(intent.get("removed") or [])
        declared_type_changes = dict(intent.get("type_changes") or {})
        new_schema = st.schema or {}
        new_cols = set(new_schema.keys())
        target_reached = (
            declared_added <= new_cols
            and not (declared_removed & new_cols)
            and all(
                new_schema.get(col) == target_type
                for col, target_type in declared_type_changes.items()
            )
        )
        if target_reached:
            st.schema_intent = None

    # ────────────────────────────────────────────────────────────────
    # Statistical drift — KL-Divergence
    # ────────────────────────────────────────────────────────────────

    def _check_statistical_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Returns ``(drift_or_None, state_was_mutated)``."""
        baseline = st.baseline
        if not baseline or not batch.rows:
            return None, False

        # BUG-564: bin each numeric column on the BASELINE's edges. Binning a
        # batch on its own min/max made bin i cover different values in the
        # two histograms, so a clean batch of another size (a wider or
        # narrower range) read as drift, and a pure shift of the whole range
        # read as no drift at all.
        bin_edges = {
            col: edges for col, dist in baseline.items()
            if (edges := self._baseline_edges(dist.histogram)) is not None
        }
        batch_dists = self._compute_distributions(batch, bin_edges=bin_edges)
        drifted_cols: List[str] = []
        kl_values: Dict[str, float] = {}
        col_stats: Dict[str, Dict[str, Any]] = {}
        max_kl = 0.0
        max_loc_shift = 0.0
        max_sig_shift = 0.0  # BUG-579: the largest shift the batch's rows establish
        kind_changed = False
        # BUG-571: a numeric column binned on the baseline's edges whose KL passed
        # zeta AND the G-test (see _aligned_kl) -- a finding that already carries
        # its own finite-sample protection, so the warmup must not hide it.
        aligned_fired = False
        # BUG-579 adversarial review: likewise a label column whose mix passed its own
        # 1e-6 G-test (_categorical_significant). A stream of micro-batches never
        # fills the window (they are too small to record), so on /uasr/ingest and
        # the MAPE-K worker the warmup hid every relabelling forever.
        tested_fired = False

        zeta = self._dynamic_threshold(st.kl_history)

        for col_name, batch_dist in batch_dists.items():
            if col_name not in baseline:
                continue

            ref_dist = baseline[col_name]
            # BUG-573: an identifier or date carries no distribution -- a sequential
            # id lies past the baseline's range on every new batch. _check_schema_drift
            # checks what can go wrong with them instead.
            if ref_dist.role in _NO_DISTRIBUTION:
                continue
            # PR #806 review: a sequence candidate's batch that continues past the
            # baseline is its next ids, judged by _format_changes' bound instead.
            if ref_dist.sequence_span and self._continues_sequence(
                    ref_dist, self._column_values(batch, col_name)):
                continue
            significant = True
            tested = False
            aligned = bool(batch_dist.histogram.get("aligned"))
            if aligned:
                kl, _ = self._aligned_kl(
                    batch_dist.histogram["counts"], ref_dist.histogram["counts"])
                # BUG-579: whether the rows establish the difference is decided by
                # _aligned_drift_p, not by the G statistic's chi-square tail, which a
                # handful of rows past the baseline's range reaches by chance.
                significant = self._aligned_drift_p(
                    batch_dist.histogram["counts"], ref_dist.histogram["counts"]) < _SIG_P
            else:
                kl = self._kl_divergence(batch_dist.histogram, ref_dist.histogram)
                if (kl != _KIND_CHANGE_KL and "categories" in batch_dist.histogram
                        and "categories" in ref_dist.histogram):
                    # BUG-579: the plug-in KL of a few labels is mostly sampling
                    # noise (one 'pending' row of a 3-label column scores ~1, past
                    # any zeta), so a label mix must also pass a 1e-6 G-test.
                    significant = self._categorical_significant(
                        batch_dist.histogram["categories"], ref_dist.histogram["categories"])
                    tested = significant
                elif kl != _KIND_CHANGE_KL:
                    # BUG-579: a caller-supplied numeric histogram with no edges is
                    # compared bin index to bin index, with no test at all; below one
                    # bin's worth of rows that comparison is noise.
                    significant = (batch_dist.sample_size or 0) >= _MIN_ROWS_PER_BIN
            # BUG-579 adversarial review: an infinite value in a column whose baseline
            # is all finite has probability 0 under any finite distribution -- proof
            # on its own, like a change of kind. The rank test drops it, and the
            # past-range test counted it as one more row past the max.
            if (
                batch_dist.mean is not None and not math.isfinite(batch_dist.mean)
                and ref_dist.mean is not None and math.isfinite(ref_dist.mean)
                and ref_dist.std is not None and math.isfinite(ref_dist.std)
                and np.isinf(self._numeric_values(batch, col_name)).any()
            ):
                kl, significant = _KIND_CHANGE_KL, True
            kind_changed = kind_changed or kl == _KIND_CHANGE_KL

            # Location / scale shift: if both have mean & std, compute a
            # normalised distance so that range shifts (e.g. 10-500 vs
            # 10 000-500 000) are detected even when histogram shapes match.
            if (
                ref_dist.mean is not None
                and batch_dist.mean is not None
                and ref_dist.std is not None
                and kl != _KIND_CHANGE_KL
            ):
                # BUG-012 (docs/BUG_REGISTRY.md): a column that is truly constant in
                # real data (e.g. improvement_surcharge, always 0.3) gets a non-zero
                # std from float rounding (numpy.std on N copies of the same float64
                # -> ~1e-17), not the mathematically-true 0. `> 0` only caught the
                # exact-zero case, so this noise floor became the denominator of a
                # "sigma" score -- turning a float-precision residual into an
                # astronomical fake sigma count that then poisons the persisted
                # kl_history the adaptive threshold reads back on every later batch.
                # Floor at _EPS, same epsilon already used for the KL calculation
                # below, so a std indistinguishable from float noise is treated as
                # the exact-zero case already handled.
                scale = ref_dist.std if ref_dist.std > _EPS else 1.0
                loc_shift = abs(batch_dist.mean - ref_dist.mean) / scale
                max_loc_shift = max(max_loc_shift, loc_shift)
                # Treat a >2-sigma shift as additional KL. BUG-579: only a shift the
                # batch's rows establish. It used to force the finding outright, so
                # one clean row 2 sigma out (a True in a 15% boolean, a lognormal's
                # tail) quarantined a micro-batch. The mean of a few rows says
                # nothing on its own. What proves a shift is an exact test that holds
                # for any distribution: _aligned_drift_p for rows past the baseline's
                # range, the rank-sum test (_rank_shift, exact on a few rows) for rows
                # inside it. /uasr/ingest, the MAPE-K worker and the recovery loop call
                # detect() alone, so a shift those tests prove must fire here.
                # BUG-579 re-review: no proof may take the baseline's SAMPLE std for
                # the true one. A Chebyshev bound on it (z std out -> 1/z^2) fired on a
                # clean 38-row lognormal(0, 2) batch against a 100-row baseline, whose
                # std understates the tail many times over (~1e-4 of such batches).
                # BUG-579 re-review: and not only past 2 sigma. While the interior has
                # too few rows for _aligned_drift_p to test it, nothing else looks at
                # how the rows inside the range moved: 40 rows of a 1.9-sigma shift
                # (rank z ~8) were clean. So the rank test runs whenever the binned
                # test could not, and a shift it proves fires at any size.
                untested = aligned and not self._interior_tested(
                    batch_dist.histogram["counts"], ref_dist.histogram["counts"])
                rank = None
                if not significant and ref_dist.std > _EPS and (loc_shift > 2.0 or untested):
                    # BUG-579 adversarial review: the values as _compute_distributions
                    # read them -- "112.50" is a number there, so it is one here.
                    arr = self._numeric_values(batch, col_name)
                    rank = self._rank_shift(arr[np.isfinite(arr)].tolist(), ref_dist)
                    significant = rank is not None
                if significant and loc_shift > 2.0:
                    kl = max(kl, loc_shift * zeta)
                    max_sig_shift = max(max_sig_shift, loc_shift)
                elif rank is not None:
                    # a proven shift of under 2 sigma: past zeta by its effect size
                    kl = max(kl, zeta * max(loc_shift, 1.0 + 2.0 * abs(rank[1] - 0.5)))

            kl_values[col_name] = round(kl, 6)

            # Preserve location/scale so the actuator can build a
            # deterministic rescale shim for unit-bug drift.
            col_stats[col_name] = {
                "baseline_mean": ref_dist.mean,
                "batch_mean": batch_dist.mean,
                "baseline_std": ref_dist.std,
            }

            # BUG-564: an aligned-histogram KL past zeta that is not
            # significant for these sample sizes is noise (see _aligned_kl).
            if kl > zeta and significant:
                drifted_cols.append(col_name)
                max_kl = max(max_kl, kl)
                aligned_fired = aligned_fired or aligned
                tested_fired = tested_fired or tested

        # Record for dynamic threshold adaptation. BUG-417: not when a column's
        # location moved by more than 2 sigma. That batch's score is the surrogate
        # `loc_shift * zeta`, so feeding it back raised zeta in proportion to itself
        # -- one unit-bug detection lifted the threshold above the largest KL a
        # histogram can produce, hiding shape drift for the next 200 batches.
        # A column that changed kind is likewise a fault, not a sample of this
        # source's ordinary noise.
        # BUG-571: nor when an aligned column fired. A scale-down moves the mean by
        # less than 2 sigma (it can never move it further than |mean|/sigma), so
        # the BUG-417 guard let its KL of 2-7 into the window and zeta rose past
        # any KL the next batches could produce.
        # BUG-579: nor from a batch too small to hold one histogram bin's worth of
        # rows. Its KL measures how few rows it has, not the source's noise (a
        # 1-row batch of a 3-label column scores ~1), and a stream of such
        # micro-batches lifted zeta until real drift in a full batch passed.
        mutated = False
        if (kl_values and max_loc_shift <= 2.0 and not kind_changed and not aligned_fired
                and len(batch.rows) >= _MIN_ROWS_PER_BIN):
            avg_kl = sum(kl_values.values()) / len(kl_values)
            st.kl_history.append(avg_kl)
            # Keep last 200 samples
            if len(st.kl_history) > 200:
                del st.kl_history[:-200]
            mutated = True

        if not drifted_cols:
            return None, mutated

        severity = DriftSeverity.LOW
        if max_kl > zeta * 3:
            severity = DriftSeverity.CRITICAL
        elif max_kl > zeta * 2:
            severity = DriftSeverity.HIGH
        elif max_kl > zeta * 1.5:
            severity = DriftSeverity.MEDIUM

        # Cold-start warmup: for the first ``warmup_batches`` numeric batches
        # per source the adaptive-ζ window is not yet full, so ζ is the flat
        # default and a 50-bin histogram estimated off ~500 rows carries
        # Poisson bin-noise large enough to push KL past ζ on a perfectly
        # healthy stream.  These false alarms are distinguishable from real
        # drift by a robust statistic: the healthy noise leaves the column
        # LOCATION unmoved (empirically <0.2σ), whereas a genuine unit bug or
        # regime change shifts the mean by many σ.  During warmup we therefore
        # still RECORD the KL sample (so the window fills) but SUPPRESS the
        # alert unless the location has genuinely moved (>2σ) — trusting the
        # robust location/scale statistic over the noisy per-bin KL while the
        # histogram is under-sampled.  Steady-state drift fires normally once
        # the window fills.  Set warmup_batches=0 to restore original behaviour.
        #
        # BUG-571: the warmup predates the BUG-564 aligned histograms. Their KL
        # is merged to >= 20 rows a bin, smoothed, and fires only past a 1e-6
        # G-test, so it is not the under-sampled noise this guards against
        # (tests/test_heal_phase1_detector.py runs it with no warmup and sees
        # zero false alarms). Gating it on the >2 sigma location shift hid every
        # scale-down -- /10 of uniform 10-500 moves the mean only 1.6 sigma --
        # and every rescale of a column centred on 0, which moves it not at all.
        # /uasr/heal judges each batch against a fresh window, so there it hid
        # them always. The warmup now only covers the legacy and categorical KL.
        if (
            self._warmup_batches
            and len(st.kl_history) <= self._warmup_batches
            and max_sig_shift <= 2.0
            and not kind_changed
            and not aligned_fired
            and not tested_fired
        ):
            return None, mutated

        return {
            "type": "statistical",
            "affected_columns": drifted_cols,
            "kl_values": kl_values,
            "col_stats": col_stats,
            "max_kl": round(max_kl, 6),
            "threshold_zeta": round(zeta, 6),
            "severity": severity,
            "details": (
                f"KL divergence exceeded ζ={zeta:.4f} in {len(drifted_cols)} column(s). "
                f"Max D_KL={max_kl:.4f}"
            ),
        }, mutated

    # ────────────────────────────────────────────────────────────────
    # Semantic drift — embedding cosine distance
    # ────────────────────────────────────────────────────────────────

    def _check_semantic_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        refs = st.embeddings
        if not refs:
            return None

        # BUG-573: the same columns the baseline's reference embedding left out --
        # unique ids and dates make every row a new token, so they read as drift.
        batch_emb = self._compute_batch_embedding(
            batch, exclude=self._no_distribution_columns(st.baseline))
        if not batch_emb:
            return None

        # Compare against reference context matrix (average distance)
        min_distance = float("inf")
        for ref_emb in refs:
            dist = self._cosine_distance(batch_emb, ref_emb)
            min_distance = min(min_distance, dist)

        if min_distance <= self._semantic_threshold:
            return None
        if not self._labels_moved(batch, st.baseline):
            return None

        severity = DriftSeverity.LOW
        if min_distance > self._semantic_threshold * 3:
            severity = DriftSeverity.CRITICAL
        elif min_distance > self._semantic_threshold * 2:
            severity = DriftSeverity.HIGH
        elif min_distance > self._semantic_threshold * 1.5:
            severity = DriftSeverity.MEDIUM

        return {
            "type": "semantic",
            "cosine_distance": round(min_distance, 6),
            "threshold": self._semantic_threshold,
            "severity": severity,
            "details": f"Semantic distance {min_distance:.4f} exceeds threshold {self._semantic_threshold}",
        }

    def _labels_moved(
        self, batch: BatchPayload, baseline: Optional[Dict[str, ColumnDistribution]],
    ) -> bool:
        """BUG-579: whether the label counts the batch embedding summarises differ from
        the baseline's beyond sampling noise.

        The embedding is a hashed count of a batch's labels, so its cosine distance
        from the reference shrinks only as the batch grows: a 5-row batch of a clean
        6-label column sits ~0.3 away, past the 0.25 threshold. The threshold says how
        far is far, never whether the batch's rows show it, so a distance counts only
        when some embedded column's labels pass the 1e-6 G-test against the baseline
        (``_categorical_significant``). A source with no baseline label counts to test
        against abstains: no verdict is better than a guess (ADR-002).
        """
        if not baseline:
            return False
        for col in self._categorical_columns(batch):
            ref = baseline.get(col)
            if ref is None or ref.role in _NO_DISTRIBUTION or "categories" not in (ref.histogram or {}):
                continue
            counts = Counter(str(v) for r in batch.rows if (v := r.get(col)) is not None)
            if counts and self._categorical_significant(counts, ref.histogram["categories"]):
                return True
        return False

    # ────────────────────────────────────────────────────────────────
    # Data-quality drift — null rate, volume, duplicates (BUG-568)
    # ------------------------------------------------------------------
    # Each threshold is set so sampling noise on clean data cannot reach it
    # (ADR-002: zero false alarms on clean data), at any batch size from a
    # tenth to ten times the baseline's: a finding needs both an effect far
    # beyond the noise of the two sample sizes involved AND a minimum effect
    # size, so a large batch cannot turn a negligible wobble into an alarm.
    # ────────────────────────────────────────────────────────────────

    def _check_null_rate_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        """A column's null rate ROSE versus the baseline's.

        Two-proportion z-test on add-one-smoothed rates, so a baseline with
        no nulls does not make a single null in a small batch look
        infinitely significant. Fires at z >= 5 (≈3e-7 per column on clean
        data) with an absolute rise of at least 5 points. A column that is
        null in every batch row counts like any other -- that is the
        blanked-column fault this exists to catch.
        """
        baseline = st.baseline
        n1 = len(batch.rows)
        if not baseline or n1 == 0:
            return None
        present = set(batch.columns) if batch.columns else {k for r in batch.rows for k in r}
        rates: Dict[str, Dict[str, float]] = {}
        declared_key = set((st.baseline_rows or {}).get("key_columns") or [])
        for col, dist in baseline.items():
            # A column the batch no longer carries is schema drift, not nulls.
            if (
                col not in present or dist.null_rate is None or dist.null_rate >= 1.0
                or not dist.sample_size
            ):
                continue
            n0 = max(int(round(dist.sample_size / (1.0 - dist.null_rate))), dist.sample_size)
            k0 = n0 - dist.sample_size
            k1 = sum(1 for r in batch.rows if r.get(col) is None)
            # BUG-573: an identifier is null-free by construction (_infer_role), so a
            # null in one is a fault however few: no sampling noise explains it.
            # heal-acc1 review: only where the values prove it is an id (a sequence
            # or a stable text code) or it is a declared key; a keyed id known by
            # its declared type alone may be an optional reference, so it gets the
            # z-test.
            exact = (
                dist.role == ROLE_IDENTIFIER and k0 == 0 and k1 > 0
                and (dist.value_bound is None or col in declared_key)
            )
            p0 = (k0 + 1) / (n0 + 2)
            p1 = (k1 + 1) / (n1 + 2)
            se = math.sqrt(p0 * (1 - p0) / (n0 + 2) + p1 * (1 - p1) / (n1 + 2))
            rise = k1 / n1 - k0 / n0
            if exact or (rise >= _NULL_MIN_RISE and (p1 - p0) / se >= _DQ_Z):
                rates[col] = {
                    "baseline_null_rate": round(k0 / n0, 6),
                    "batch_null_rate": round(k1 / n1, 6),
                }
        if not rates:
            return None
        max_rise = max(r["batch_null_rate"] - r["baseline_null_rate"] for r in rates.values())
        return {
            "type": "null_rate",
            "affected_columns": list(rates),
            "null_rates": rates,
            "severity": self._fraction_severity(max_rise),
            "details": "Null rate rose in column(s): " + ", ".join(
                f"{c} {r['baseline_null_rate']:.1%} -> {r['batch_null_rate']:.1%}"
                for c, r in rates.items()
            ),
        }

    def _check_volume_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        """Row count of a whole run more than 100x below or above the baseline run's.

        BUG-568 review: volume is a property of a RUN (or period), never of a
        micro-batch. Real callers only cap batch size -- the connections
        ingest bridge pages a table in fixed batch_size chunks, so its last
        page is short; a quiet Kafka poll window yields a handful of rows --
        so comparing a micro-batch with the baseline batch alarmed on clean
        data. The check therefore runs only when BOTH the batch and the
        baseline declare themselves a complete run
        (``metadata["complete_run"] is True``). Anything undeclared,
        including a distributions-only baseline whose run size is unknown,
        is never judged: no guessing (ADR-002).
        """
        if not _is_complete_run(batch):
            return None
        profile = st.baseline_rows
        if not profile or not profile.get("complete_run"):
            return None
        # ADR-002 phase 2b: run sizes, never page sizes (``_run_rows``).
        n0 = int(profile.get("run_rows", profile["rows"]))
        if n0 < _VOLUME_MIN_BASELINE_ROWS:
            return None
        n1 = _run_rows(batch)
        if n0 <= n1 * _VOLUME_FACTOR and n1 <= n0 * _VOLUME_FACTOR:
            return None
        return {
            "type": "volume",
            "affected_columns": [],
            "baseline_rows": n0,
            "batch_rows": n1,
            "severity": DriftSeverity.CRITICAL if n1 == 0 else DriftSeverity.HIGH,
            "details": f"Run row count {n1} vs baseline run {n0} (more than {_VOLUME_FACTOR}x apart)",
        }

    @staticmethod
    def _equal_row_tuples(batch: BatchPayload, columns: Optional[List[str]] = None) -> Tuple[int, int]:
        """Row pairs and row triples in the batch that are exact duplicates over ``columns``."""
        cols = columns or batch.columns or (list(batch.rows[0].keys()) if batch.rows else [])
        counts = Counter(repr(tuple(r.get(c) for c in cols)) for r in batch.rows)
        pairs = sum(c * (c - 1) // 2 for c in counts.values())
        triples = sum(c * (c - 1) * (c - 2) // 6 for c in counts.values())
        return pairs, triples

    @classmethod
    def _equal_row_pairs(cls, batch: BatchPayload, columns: Optional[List[str]] = None) -> int:
        """Number of row pairs in the batch that are exact duplicates."""
        return cls._equal_row_tuples(batch, columns)[0]

    @staticmethod
    def _expected_pairs_bound(n0: int, k0: int, t0: int, n1: int) -> float:
        """An upper bound on the equal row pairs a clean ``n1``-row batch shows, given
        a baseline of ``n0`` rows with ``k0`` equal pairs and ``t0`` equal triples
        (BUG-574).

        Equal pairs are a U-statistic: with s2 = sum p^2 and s3 = sum p^3 over the
        row distribution, E[K] = C(n,2)*s2 and
        Var[K] = C(n,2)*s2*(1-s2) + 6*C(n,3)*(s3 - s2^2). The second term is why a
        Poisson bound is not enough: on low-cardinality rows one common value makes
        thousands of pairs at once, so K is overdispersed by D = (1-s2) +
        2(n-2)(s3-s2^2)/s2. The baseline's own s2 and s3 (unbiased, from k0 and t0)
        give D for both samples; k0 is raised by a Poisson upper bound plus five
        overdispersed standard deviations, scaled to the batch's size, and five
        more of the batch's own standard deviations are added on top.

        On a handful of distinct rows K is a scaled chi-square with few degrees of
        freedom, whose upper tail is far longer than five normal deviations, so the
        bound never drops below the BUG-568 floor (twice the scaled Poisson bound,
        plus 10 pairs): that floor sits past the 1e-6 chi-square tail down to one
        degree of freedom (tests/test_heal_accuracy_bug573_574.py measures it).
        """
        m0, m1 = n0 * (n0 - 1) / 2, n1 * (n1 - 1) / 2
        s2 = k0 / m0
        s3 = t0 / (n0 * (n0 - 1) * (n0 - 2) / 6) if n0 >= 3 else 0.0

        def dispersion(n: int) -> float:
            if s2 <= 0:
                return 1.0
            return max(1.0, (1 - s2) + 2 * (n - 2) * max(s3 - s2 * s2, 0.0) / s2)

        poisson_upper = (math.sqrt(k0 + 1) + 3) ** 2
        expected = (poisson_upper + 5 * math.sqrt(k0 * (dispersion(n0) - 1))) * m1 / m0
        return max(expected + 5 * math.sqrt(expected * dispersion(n1)), 2 * poisson_upper * m1 / m0 + 10)

    def _check_duplicate_drift(
        self, batch: BatchPayload, st: SourceState,
    ) -> Optional[Dict[str, Any]]:
        """Exact-duplicate rows (on the declared key, else the full row) the baseline
        does not explain, or a repeated value in an identifier column (BUG-574).

        * A declared key (the baseline's ``metadata.key_columns``) is unique by
          declaration (ADR-002 "exact duplicates on the declared key"): any key value
          repeated in the batch, or already delivered by an earlier page of the same
          run (``metadata.run_id``, ``_RunKeys``), is a fault -- exact, every time,
          whatever the baseline held. Key values compare as ``_key_part`` reads them
          ("10005" is 10005); a row whose key holds a null or NaN carries no key
          value (the null-rate check judges it).
        * No key, but the baseline had no duplicate and an identifier column makes its
          rows unique by construction: any duplicate row in a batch is a fault --
          exact, with no statistical floor, which let 15 copies in 300 rows through.
        * An identifier column (BUG-573) repeats a value: the same, exactly.
        * Otherwise the batch's equal-pair count must exceed what the baseline's
          own repeat rate explains at the batch's size (_expected_pairs_bound).
          Comparing duplicate RATES directly would be wrong: data with a finite
          set of possible rows repeats more the more rows you draw, so a clean 10x
          batch has a far higher duplicate rate than its baseline.

        Detection only (ADR-002): no row is dropped or changed. Nothing here records
        keys: a page's delivered rows join its run in ``record_delivered_keys``.
        """
        profile = st.baseline_rows
        if not profile or not profile.get("columns"):
            return None
        present = set(batch.columns) if batch.columns else {k for r in batch.rows for k in r}
        n1 = len(batch.rows)
        repeated_ids: Dict[str, int] = {}
        for col, dist in (st.baseline or {}).items():
            # A keyed id (value_bound set) is unique in the baseline but could be a
            # wide measure that repeats by chance; its duplicated rows still alarm.
            if (
                dist.role == ROLE_IDENTIFIER and dist.value_bound is None and col in present
            ):
                # A missing value (None, NaN) is no id value to repeat (PR #806 review 6).
                values = [repr(v) for r in batch.rows
                          if (v := r.get(col)) is not None and not (isinstance(v, float) and v != v)]
                if len(set(values)) < len(values):
                    repeated_ids[col] = len(values) - len(set(values))
        key = list(profile.get("key_columns") or [])
        cols = key or list(profile["columns"])
        k1, rows_fault, expected_upper, dup_rows, in_run = 0, False, None, 0, 0
        # A removed column makes rows less unique: schema drift's job.
        if key and set(key) <= present:
            keys = _row_keys(batch.rows, key)
            counts = Counter(keys)
            dup_rows = len(keys) - len(counts)
            k1 = sum(c * (c - 1) // 2 for c in counts.values())
            run_id = (batch.metadata or {}).get("run_id")
            if run_id is not None and counts:
                in_run = self._run_keys.seen(
                    batch.source_id, str(run_id), batch.batch_id, _key_hashes(list(counts)))
            expected_upper = 0.0
            rows_fault = dup_rows > 0 or in_run > 0
        elif not key and set(cols) <= present and n1 >= 2:
            n0, k0 = int(profile["rows"]), int(profile["equal_pairs"])
            k1 = self._equal_row_pairs(batch, cols)
            if k0 == 0 and profile.get("unique_columns"):
                expected_upper = 0.0
            elif n0 >= 2:
                expected_upper = self._expected_pairs_bound(
                    n0, k0, int(profile.get("equal_triples") or 0), n1)
            rows_fault = expected_upper is not None and k1 > expected_upper
            if rows_fault:
                dup_rows = n1 - len({repr(tuple(r.get(c) for c in cols)) for r in batch.rows})
        if not rows_fault and not repeated_ids:
            return None
        dup_fraction = (dup_rows + in_run) / n1
        id_fraction = max(repeated_ids.values(), default=0) / n1
        parts = []
        if key and dup_rows:
            parts.append(f"{dup_rows} row(s) repeat a value of the declared key {key} in the batch")
        if in_run:
            parts.append(f"{in_run} value(s) of the declared key {key} were already delivered earlier "
                         f"in run {(batch.metadata or {}).get('run_id')!r}")
        if rows_fault and not key:
            parts.append(
                f"{dup_rows} duplicate row(s) ({dup_fraction:.1%}); {k1} equal pairs vs at most "
                f"{expected_upper:.1f} expected from the baseline")
        if repeated_ids:
            parts.append("repeated values in identifier column(s): " + ", ".join(
                f"{c} ({k} repeat(s))" for c, k in sorted(repeated_ids.items())))
        return {
            "type": "duplicate",
            "affected_columns": sorted(repeated_ids),
            "equal_pairs": k1,
            "expected_pairs_upper": round(expected_upper, 3) if expected_upper is not None else None,
            "duplicate_rows": dup_rows,
            "duplicate_fraction": round(dup_fraction, 6),
            "repeated_identifiers": repeated_ids,
            "key_columns": key,
            "keys_seen_earlier_in_run": in_run,
            "severity": self._fraction_severity(max(dup_fraction, id_fraction)),
            "details": "; ".join(parts),
        }

    def record_delivered_keys(self, batch: BatchPayload, rows: List[Dict[str, Any]]) -> None:
        """The rows a page delivered join its run's declared-key memory (PR #806 review
        4). /uasr/heal calls it once it has decided, with the rows it returns: a held
        row was never delivered, so its key is no duplicate of anything sent later
        (a page quarantined and re-sent corrected used to be flagged). A page that
        declares ``complete_run`` ends the run."""
        run_id = (batch.metadata or {}).get("run_id")
        if run_id is None:
            return
        st = self._store.peek(batch.source_id)
        key = list(((st.baseline_rows if st else None) or {}).get("key_columns") or [])
        if not key:
            return
        self._run_keys.record(batch.source_id, str(run_id), batch.batch_id,
                              _key_hashes(list(set(_row_keys(rows, key)))), end=_is_complete_run(batch))

    @staticmethod
    def _fraction_severity(fraction: float) -> DriftSeverity:
        if fraction >= 0.5:
            return DriftSeverity.CRITICAL
        if fraction >= 0.25:
            return DriftSeverity.HIGH
        if fraction >= 0.1:
            return DriftSeverity.MEDIUM
        return DriftSeverity.LOW

    # ────────────────────────────────────────────────────────────────
    # KL-Divergence computation
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _kl_divergence(p_hist: Dict[str, Any], q_hist: Dict[str, Any]) -> float:
        """
        Compute KL(P || Q) from two histogram dicts.

        Histogram format: {"bins": [...], "counts": [...]}
        For categorical: {"categories": {"a": 10, "b": 5}}
        Plain dicts like {"a": 0.9, "b": 0.1} are also accepted as
        categorical distributions (keys → categories, values → weights).
        """
        # Detect plain categorical dict (no special keys)
        _special = {"categories", "bins", "counts"}
        p_is_plain = bool(p_hist) and not (set(p_hist.keys()) & _special)
        q_is_plain = bool(q_hist) and not (set(q_hist.keys()) & _special)

        # Normalise to {"categories": {...}} form
        if p_is_plain:
            p_hist = {"categories": p_hist}
        if q_is_plain:
            q_hist = {"categories": q_hist}

        # Categorical histograms
        if "categories" in p_hist and "categories" in q_hist:
            p_cats = p_hist["categories"]
            q_cats = q_hist["categories"]
            all_keys = set(p_cats.keys()) | set(q_cats.keys())

            p_total = sum(p_cats.values()) or 1
            q_total = sum(q_cats.values()) or 1

            kl = 0.0
            for k in all_keys:
                p_val = (p_cats.get(k, 0) / p_total) + _EPS
                q_val = (q_cats.get(k, 0) / q_total) + _EPS
                kl += p_val * math.log(p_val / q_val)
            return max(kl, 0.0)

        # Numeric histograms with bins/counts
        p_counts = p_hist.get("counts", [])
        q_counts = q_hist.get("counts", [])

        # BUG-418: one side numeric, the other categorical -- the column changed
        # kind (a numeric column now carries text, or the reverse). The two share no
        # support, so this is the largest divergence the epsilon floor allows; it used
        # to fall through to the empty-counts case below and score 0.
        if (p_counts and "categories" in q_hist) or (q_counts and "categories" in p_hist):
            return _KIND_CHANGE_KL

        if not p_counts or not q_counts:
            return 0.0

        # Align lengths
        max_len = max(len(p_counts), len(q_counts))
        p_arr = list(p_counts) + [0] * (max_len - len(p_counts))
        q_arr = list(q_counts) + [0] * (max_len - len(q_counts))

        p_total = sum(p_arr) or 1
        q_total = sum(q_arr) or 1

        kl = 0.0
        for i in range(max_len):
            p_val = (p_arr[i] / p_total) + _EPS
            q_val = (q_arr[i] / q_total) + _EPS
            kl += p_val * math.log(p_val / q_val)

        return max(kl, 0.0)

    # ────────────────────────────────────────────────────────────────
    # Dynamic threshold
    # ────────────────────────────────────────────────────────────────

    def _dynamic_threshold(self, history: List[float]) -> float:
        """
        Adaptive ζ based on historical KL variance.

        ζ = mean(D_KL) + 2·std(D_KL), floored at HALF the default (not the
        full default -- BUG-071: this docstring previously said "floored at
        the default", but the `* 0.5` below has been the actual, long-
        standing implementation since this method's introduction, with
        existing tests asserting the resulting sensitivity. Since the
        behavior is what production has always run and is exercised by
        tests, this fix corrects the doc to match the code rather than
        changing live detection thresholds).

        Once a source has ≥5 KL samples, this lets ζ settle as low as
        `default_zeta / 2` for a genuinely low-noise source -- up to 2x
        more sensitive than the flat default before enough history exists.
        """
        if len(history) < 5:
            return self._default_zeta

        mean_kl = sum(history) / len(history)
        var_kl = sum((x - mean_kl) ** 2 for x in history) / len(history)
        std_kl = math.sqrt(var_kl)

        adaptive = mean_kl + 2 * std_kl
        return max(adaptive, self._default_zeta * 0.5)

    # ────────────────────────────────────────────────────────────────
    # Distribution computation from raw batch
    # ────────────────────────────────────────────────────────────────

    def _compute_distributions(
        self,
        batch: BatchPayload,
        bin_edges: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, ColumnDistribution]:
        """Build column-level distributions from batch rows.

        ``bin_edges`` maps a column to a baseline's histogram edges; a numeric
        column listed there is binned on those edges (see
        ``_histogram_on_edges``) instead of its own range.
        """
        if not batch.rows:
            return {}

        columns = batch.columns or (list(batch.rows[0].keys()) if batch.rows else [])
        result: Dict[str, ColumnDistribution] = {}

        for col in columns:
            values = [row.get(col) for row in batch.rows if row.get(col) is not None]
            if not values:
                continue

            dist = ColumnDistribution(
                column_name=col, sample_size=len(values), value_kind=self._value_kind(values))

            # Numeric vs categorical: attempt a single vectorized float cast.
            # np.asarray(..., dtype=float) raises if ANY value is non-numeric,
            # preserving the previous all-or-nothing type detection while
            # replacing three O(n) Python passes (per-element float(),
            # sum-of-squares, set()) with GIL-releasing numpy kernels.
            numeric_arr = None
            try:
                numeric_arr = np.asarray(values, dtype=float)
            except (ValueError, TypeError):
                numeric_arr = None

            if numeric_arr is not None and numeric_arr.size:
                # Numeric distribution (population std, ddof=0 — matches prior)
                dist.mean = float(numeric_arr.mean())
                dist.std = float(numeric_arr.std())
                dist.null_rate = 1.0 - len(values) / max(len(batch.rows), 1)
                distinct = np.unique(numeric_arr)
                dist.distinct_count = int(distinct.size)
                dist.decimals = self._recorded_decimals(distinct)
                if bin_edges and col in bin_edges:
                    dist.histogram = self._histogram_on_edges(numeric_arr, bin_edges[col])
                else:
                    dist.histogram = self._build_numeric_histogram(numeric_arr)
            else:
                # Categorical distribution
                str_vals = [str(v) for v in values]
                counts = Counter(str_vals)
                dist.distinct_count = len(counts)
                dist.null_rate = 1.0 - len(values) / max(len(batch.rows), 1)
                dist.histogram = {"categories": dict(counts)}

            result[col] = dist

        return result

    @staticmethod
    def _build_numeric_histogram(values, bins: int = _DEFAULT_BINS) -> Dict[str, Any]:
        """Build a simple equal-width histogram.

        Accepts a list or a numpy array. Bins ``min(int((v - min) / width), bins - 1)``
        via np.bincount. BUG-579 adversarial review: ``width`` is ``_edge_width`` of the
        stored edges, the one ``_histogram_on_edges`` re-derives, not (max - min) / bins,
        which can differ from it by an ulp -- a baseline 65.58 sat in bin 4 and a batch
        65.58 in bin 5, so a pair the rank test must count as tied came out ordered.
        """
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return {"bins": [], "counts": []}

        min_val = float(arr.min())
        max_val = float(arr.max())

        if min_val == max_val:
            return {"bins": [min_val], "counts": [int(arr.size)]}

        bin_width = (max_val - min_val) / bins
        bin_edges = [min_val + i * bin_width for i in range(bins + 1)]
        width = DriftDetector._edge_width(bin_edges)
        idx = np.minimum(((arr - min_val) / width).astype(np.int64), bins - 1)
        counts = np.bincount(idx, minlength=bins)[:bins].astype(int).tolist()

        return {"bins": bin_edges, "counts": counts}

    @staticmethod
    def _edge_width(edges: List[float]) -> float:
        """The bin width both a baseline and a batch binned on its edges use."""
        return (edges[-1] - edges[0]) / (len(edges) - 1)

    @staticmethod
    def _baseline_edges(hist: Dict[str, Any]) -> Optional[List[float]]:
        """The edges of a numeric histogram built by ``_build_numeric_histogram``.

        ``bins`` holds len(counts)+1 edges, or a single value for a constant
        column. A histogram of any other shape (a caller-supplied dict) has
        no usable edges and keeps the legacy index-wise comparison.
        """
        edges = hist.get("bins")
        counts = hist.get("counts")
        if not edges or not counts:
            return None
        if len(edges) == len(counts) + 1 or (len(edges) == 1 and len(counts) == 1):
            return [float(e) for e in edges]
        return None

    @staticmethod
    def _histogram_on_edges(values, edges: List[float]) -> Dict[str, Any]:
        """Counts of ``values`` on a baseline's edges, plus an underflow and an
        overflow bin (BUG-564).

        Interior bins use the same ``min(int((v - lo) / width), bins - 1)``
        rule that built the baseline, so the baseline's own rows re-bin to its
        own counts. Values outside the baseline's range land in the end bins
        instead of stretching the bins, which is what lets a shift or a
        widening of the range show up as divergence. A constant baseline
        (one edge) gets three bins: below, equal to, above that value.
        """
        arr = np.asarray(values, dtype=float)
        if len(edges) == 1:
            v = edges[0]
            counts = [int((arr < v).sum()), int((arr == v).sum()), int((arr > v).sum())]
            return {"bins": edges, "counts": counts, "aligned": True}

        lo, hi = edges[0], edges[-1]
        nbins = len(edges) - 1
        width = DriftDetector._edge_width(edges)
        # The stored top edge is lo + nbins * width in floating point, which
        # can land a few ulps below the baseline's true max; tolerate that so
        # the baseline's own max value is not counted as overflow.
        hi_tol = hi + 4 * float(np.spacing(abs(hi)))
        under = arr < lo
        over = arr > hi_tol
        inside = arr[~(under | over)]
        idx = np.clip(((inside - lo) / width).astype(np.int64), 0, nbins - 1)
        inner = np.bincount(idx, minlength=nbins)[:nbins].astype(int).tolist()
        counts = [int(under.sum()), *inner, int(over.sum())]
        return {"bins": edges, "counts": counts, "aligned": True}

    @staticmethod
    def _aligned_kl(p_counts: List[int], q_inner_counts: List[int]) -> Tuple[float, bool]:
        """KL(P || Q) for a batch binned by ``_histogram_on_edges`` against the
        baseline counts it was binned on (BUG-564), and whether that
        divergence is statistically significant.

        Three finite-sample corrections keep a clean batch of ANY size from
        firing:

        * Adjacent baseline bins are merged so each holds about
          ``_MIN_ROWS_PER_BIN`` rows of the smaller of the two samples. The
          plug-in KL of an n-row sample over k bins is biased upward by about
          (k-1)/2n even when nothing changed -- ~0.25 for 100 rows over 50
          bins, past the default zeta of 0.15 on its own.
        * The baseline gets empty under/overflow bins and a 0.5 pseudo-count
          in every bin. A bin the baseline happened to leave empty (a sparse
          tail, the range just past its max) does not have probability zero;
          with the bare 1e-10 floor used elsewhere, ONE clean value there cost
          (1/n)·ln(1/(n·1e-10)) -- ≈0.18 at n=100.
        * Significance: values past either end of the baseline's range are
          significant when a clean batch would show that many with chance
          below 1e-6 (``_end_rows_p``, exact). The in-range bins: 2·n_eff·KL
          (the G statistic, n_eff = n0·n1/(n0+n1)) is ~chi-square(bins-1) when
          nothing changed. A KL above zeta from a 20-row batch is mostly noise,
          so the caller only lets the KL fire when one of the two is significant.

        Real change still diverges strongly: a 1-sd shift of a normal column
        scores ≈0.3 or more, and mass past the baseline's range far more.
        """
        p = np.asarray(p_counts, dtype=float)
        q_inner = np.asarray(q_inner_counts, dtype=float)
        if len(p) != len(q_inner) + 2:
            return 0.0, False
        n1, n0 = p.sum(), q_inner.sum()
        if n1 <= 0 or n0 <= 0:
            return 0.0, False
        nbins = len(q_inner)
        groups = int(min(nbins, max(1, min(n0, n1) // _MIN_ROWS_PER_BIN)))
        width = math.ceil(nbins / groups)
        starts = np.arange(0, nbins, width)
        p = np.concatenate(([p[0]], np.add.reduceat(p[1:-1], starts), [p[-1]]))
        q_raw = np.concatenate(([0.0], np.add.reduceat(q_inner, starts), [0.0]))
        q = (q_raw + 0.5) / (q_raw.sum() + 0.5 * len(q_raw))
        kl = DriftDetector._plugin_kl(p / n1, q)
        # BUG-577: the under/overflow bins are judged exactly, not by the G test.
        # The baseline leaves them empty by construction (its own min and max are
        # the edges), so their expected batch counts are far below the chi-square
        # approximation's reach: 6 of 27 clean values below a 106-row baseline's
        # min scored past the "1e-6" quantile, though such a batch occurs about
        # once in 23,000 (two clean false alarms in 1,300 real batches). The inner
        # bins hold >= _MIN_ROWS_PER_BIN rows of the smaller sample, where the G
        # test holds, and are tested on their own, conditioned on the rows in range.
        if _end_rows_p(int(p[0]), int(p[-1]), int(n1), int(n0)) < _SIG_P:
            return kl, True
        p_in, q_in = p[1:-1], q_raw[1:-1]
        m = p_in.sum()
        df = int(np.count_nonzero((p_in > 0) | (q_in > 0))) - 1
        if m <= 0 or df < 1:
            return kl, False
        q_in = (q_in + 0.5) / (q_in.sum() + 0.5 * len(q_in))
        g_stat = 2.0 * DriftDetector._plugin_kl(p_in / m, q_in) * (n0 * m / (n0 + m))
        return kl, bool(g_stat > _chi2_crit(df))

    @staticmethod
    def _categorical_significant(p_cats: Dict[str, Any], q_cats: Dict[str, Any]) -> bool:
        """Whether a batch's label counts differ from the baseline's at the 1e-6 level
        (BUG-579): the G-test of ``_aligned_kl`` over the labels, with the same 0.5
        pseudo-count, so a label the baseline never saw is rare, not impossible.

        One row can never pass it (G = 2 ln(2 n0) at most, below the df=1 quantile
        for any baseline under 10^5 rows); a handful of rows that all carry labels
        the baseline never had (a relabelled column) does.
        """
        keys = sorted(set(p_cats) | set(q_cats))
        p = np.asarray([float(p_cats.get(k, 0)) for k in keys])
        q_raw = np.asarray([float(q_cats.get(k, 0)) for k in keys])
        n1, n0 = p.sum(), q_raw.sum()
        if n1 <= 0 or n0 <= 0:
            return False
        q = (q_raw + 0.5) / (n0 + 0.5 * len(keys))
        p = p / n1
        nz = p > 0
        kl = max(float(np.sum(p[nz] * np.log(p[nz] / q[nz]))), 0.0)
        g_stat = 2.0 * kl * (n0 * n1 / (n0 + n1))
        return bool(g_stat > _chi2_crit(max(len(keys) - 1, 1)))

    @staticmethod
    def _numeric_values(batch: BatchPayload, col: str) -> np.ndarray:
        """A numeric column's non-null values cast to float exactly as
        ``_compute_distributions`` cast them (numeric strings included); call it
        only for a column it found numeric."""
        return np.asarray([v for row in batch.rows if (v := row.get(col)) is not None], dtype=float)

    @staticmethod
    def _interior_groups(n0: float, n1_in: float, nbins: int) -> int:
        """How many merged interior bins ``_aligned_drift_p`` tests: >= _MIN_ROWS_PER_BIN
        rows of the smaller sample each; fewer than 2 is no test."""
        return int(min(nbins, max(1, min(n0, n1_in) // _MIN_ROWS_PER_BIN)))

    @staticmethod
    def _interior_tested(p_counts: List[int], q_inner_counts: List[int]) -> bool:
        """Whether ``_aligned_drift_p`` tests the batch's rows inside the baseline's
        range at all (BUG-579 re-review); when it cannot, _rank_shift does."""
        if len(p_counts) != len(q_inner_counts) + 2:
            return True  # not an aligned histogram: nothing for the rank test to add
        n1_in = float(sum(p_counts[1:-1]))
        n0 = float(sum(q_inner_counts))
        return DriftDetector._interior_groups(n0, n1_in, len(q_inner_counts)) >= 2

    @staticmethod
    def _aligned_drift_p(p_counts: List[int], q_inner_counts: List[int]) -> float:
        """The detector's p-value that a batch binned by ``_histogram_on_edges`` comes
        from the baseline whose counts it was binned on (BUG-579).

        ``_aligned_kl``'s G test is chi-square only while every bin expects a few
        rows. The under/overflow bins never do: a clean value lands past the
        baseline's max with probability 1/(n0+1), so two such rows in a 2-row batch
        (p ~ 1e-5) scored past its 1e-6 quantile, and a lognormal's tail fired on
        micro-batches. So the two parts are tested apart and combined (Fisher):

        * the ends: under exchangeability, P(the a lowest and the k-a highest of
          the n0+n1 values all come from the batch) = prod_{i<k} (n1-i)/(n0+n1-i),
          with k the batch's rows below the baseline's min plus those above its
          max; times k+1 for the splits a = 0..k (union bound);
        * the values inside the range by the G test over the merged interior bins
          (>= _MIN_ROWS_PER_BIN rows of the smaller sample each), conditional on
          how many are inside. With fewer than that there is one bin and no test.

        ``_aligned_kl`` stays the repair prover's test: there "not significant"
        is the evidence a repair restored a column, so it must not get easier.
        """
        p = np.asarray(p_counts, dtype=float)
        q_inner = np.asarray(q_inner_counts, dtype=float)
        if len(p) != len(q_inner) + 2:
            return 1.0
        n1, n0 = float(p.sum()), float(q_inner.sum())
        if n1 <= 0 or n0 <= 0:
            return 1.0
        # BUG-579 review: the union bound over the k+1 splits keeps it valid.
        p_end = _end_rows_p(int(p[0]), int(p[-1]), int(n1), int(n0))
        inner = p[1:-1]
        n1_in = float(inner.sum())
        nbins = len(q_inner)
        groups = DriftDetector._interior_groups(n0, n1_in, nbins)
        if groups < 2:
            return p_end
        starts = np.arange(0, nbins, math.ceil(nbins / groups))
        pi = np.add.reduceat(inner, starts) / n1_in
        qr = np.add.reduceat(q_inner, starts)
        q = (qr + 0.5) / (n0 + 0.5 * len(qr))
        nz = pi > 0
        kl = max(float(np.sum(pi[nz] * np.log(pi[nz] / q[nz]))), 0.0)
        df = max(int(np.count_nonzero((pi > 0) | (qr > 0))) - 1, 1)
        p_in = _chi2_sf(2.0 * kl * (n0 * n1_in / (n0 + n1_in)), df)
        # Fisher's method for two independent p-values: chi-square with 4 df.
        x = -2.0 * (math.log(max(p_in, 1e-300)) + math.log(max(p_end, 1e-300)))
        return math.exp(-x / 2) * (1 + x / 2)

    @staticmethod
    def _rank_shift(values: List[float], dist: ColumnDistribution) -> Optional[Tuple[float, float]]:
        """(z, P(new > baseline)) when the finite ``values`` sit significantly
        and materially (|effect - 0.5| >= _SHIFT_EFFECT) above or below the numeric
        baseline ``dist``, else None -- the Mann-Whitney rank-sum test on the
        baseline's own bins, ties corrected for (BUG-575; see
        ``actuator_agent.location_shift``). It holds whatever the column's shape, so
        for a heavy tail as for a normal. Significant is a conservative exact
        p < _SIG_P while either sample has at most _EXACT_RANK_ROWS rows
        (``_rank_sum_significant``, BUG-579 re-review), |z| > _SHIFT_Z by the normal
        approximation when both have more.

        A unique int column is an identifier (BUG-573): never shifted.
        """
        if dist.mean is None:
            return None
        edges = DriftDetector._baseline_edges(dist.histogram or {})
        if edges is None:
            return None
        n0 = int(dist.sample_size or 0)
        if dist.value_kind == "int" and n0 > 1 and dist.distinct_count == n0:
            return None
        if len(values) < 2:
            return None
        p = np.asarray(DriftDetector._histogram_on_edges(values, edges)["counts"], dtype=float)
        q = np.concatenate(([0.0], np.asarray(dist.histogram["counts"], dtype=float), [0.0]))
        if len(p) != len(q):
            return None
        n1, n0f = float(p.sum()), float(q.sum())
        total = n1 + n0f
        if n1 < 2 or n0f < 2:
            return None
        tied = p + q
        midrank = np.cumsum(tied) - tied + (tied + 1) / 2
        u = float((p * midrank).sum()) - n1 * (n1 + 1) / 2
        var = n0f * n1 / 12 * ((total + 1) - float((tied ** 3 - tied).sum()) / (total * (total - 1)))
        if var <= 0:
            return None
        z = (u - n0f * n1 / 2) / math.sqrt(var)
        effect = u / (n0f * n1)
        if abs(effect - 0.5) < _SHIFT_EFFECT:
            return None
        if min(n1, n0f) <= _EXACT_RANK_ROWS:
            # BUG-579 re-review: the normal cut is unreachable on a few rows and wrong
            # in the tail of heavily tied scores -- on either side: a 3-row boolean
            # baseline against 100 clean rows scored z = -5.7 (exact p 0.007).
            exact = DriftDetector._raw_value_counts(values, dist, edges)
            if exact is not None:
                # BUG-579 adversarial review: the baseline's bins hold its raw values
                # (a 0/1 or small-int column), so the ties are real and the exact
                # tie-respecting test is valid -- the bins' edges never enter it. The
                # conservative test below counts every tie as no evidence, which in a
                # 0/1 column is nearly all of it: a 15% flag flipped to 85% in 10-30
                # rows went uncaught.
                significant = DriftDetector._exact_tied_rank_p(*exact) < _SIG_P
            else:
                counts_p = np.rint(p).astype(np.int64)
                counts_q = np.rint(q).astype(np.int64)
                below = np.cumsum(counts_q) - counts_q
                above = int(counts_q.sum()) - np.cumsum(counts_q)
                u_strict = int((counts_p * (below if effect > 0.5 else above)).sum())
                significant = DriftDetector._rank_sum_significant(
                    int(counts_p.sum()), int(counts_q.sum()), u_strict)
        else:
            significant = abs(z) > _SHIFT_Z
        return (z, effect) if significant else None

    @staticmethod
    def _raw_value_counts(
        values: List[float], dist: ColumnDistribution, edges: List[float],
    ) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """(batch counts, baseline counts) over the distinct raw values of both samples,
        when the baseline's histogram records its values exactly (BUG-579 adversarial
        review), else None.

        It does for an int or bool column whose range is shorter than its bin count:
        no bin can then hold two integers, so each non-empty bin is one value. The
        batch must be whole numbers too; its values outside the baseline's range are
        values of their own. At most _EXACT_TIED_VALUES distinct values, to bound the
        exact test's cost; a wider column has few ties and the conservative test
        loses little on it.
        """
        if dist.value_kind not in ("int", "bool") or len(edges) < 2:
            return None
        lo, hi, nbins = edges[0], edges[-1], len(edges) - 1
        # the top edge is lo + bins * width in floating point: the baseline's max give
        # or take an ulp or two
        top = round(hi)
        if not float(lo).is_integer() or abs(hi - top) > 1e-9 * max(1.0, abs(hi)) or top - lo >= nbins:
            return None
        arr = np.asarray(values, dtype=float)
        if arr.size == 0 or not np.array_equal(arr, np.floor(arr)):
            return None
        ints = np.arange(int(lo), int(top) + 1)
        idx = np.minimum(((ints - lo) / DriftDetector._edge_width(edges)).astype(np.int64), nbins - 1)
        counts = np.rint(np.asarray(dist.histogram["counts"], dtype=float)).astype(np.int64)
        if len(np.unique(idx)) != len(idx) or len(counts) != nbins:
            return None
        base = counts[idx]
        if int(base.sum()) != int(counts.sum()):
            return None  # a non-empty bin holds no integer: not a whole-number column
        batch_values, batch_counts = np.unique(arr, return_counts=True)
        cats = np.union1d(ints[base > 0], batch_values)
        if len(cats) > _EXACT_TIED_VALUES:
            return None
        p_counts = np.zeros(len(cats), dtype=np.int64)
        p_counts[np.searchsorted(cats, batch_values)] = batch_counts
        q_counts = np.zeros(len(cats), dtype=np.int64)
        q_counts[np.searchsorted(cats, ints[base > 0])] = base[base > 0]
        return p_counts, q_counts

    @staticmethod
    def _exact_tied_rank_p(p_counts, q_counts) -> float:
        """Exact two-sided p-value of the rank-sum test of a batch (``p_counts``) against a
        baseline (``q_counts``) counted over the same distinct raw values, ties and all
        (BUG-579 adversarial review).

        Each value j scores its midrank r_j in the pooled sample. Under the null either
        sample is a uniformly random subset of the N pooled rows, whatever the
        column's shape: P(x_j of the m rows in value j for all j) = prod_j C(t_j, x_j)
        / C(N, m), with t_j the pooled rows of value j. The values are the data's own,
        not bins cut on one sample's range, so the pooled counts are the same under
        every relabelling and conditioning on them is exact. The statistic is the
        smaller sample's W = sum_j x_j s_j, s_j = r_j on an integer grid of about
        _EXACT_TIED_GRID units (2 r_j itself while the pool is small enough); the
        scores depend only on the pooled counts, so rounding them changes the
        statistic, never the validity. The p-value is P(|W - E W| >= |w - E W|), both
        tails counted (``_tied_tail``): for a skewed 0/1 column the far tail is often
        empty, where doubling one tail would throw half the evidence away. A
        Chernoff bound on the two tails (``_tied_chernoff``) settles a clear shift
        before the count runs; it is never under the exact p-value.
        """
        p = np.asarray(p_counts, dtype=np.int64)
        q = np.asarray(q_counts, dtype=np.int64)
        if p.sum() > q.sum():  # |W - E W| is the same for either sample
            p, q = q, p
        t = p + q
        n1, total = int(p.sum()), int(t.sum())
        if n1 <= 0 or n1 >= total:
            return 1.0
        rank2 = 2 * np.cumsum(t) - t + 1  # twice the midrank
        per_row = max(1, _EXACT_TIED_GRID // n1)
        if 2 * total <= per_row:
            score = rank2
        else:
            score = np.rint(rank2 * (per_row / (2.0 * total))).astype(np.int64)
        w_obs = float((p * score).sum())
        mean = n1 * float((t * score).sum()) / total
        dist = abs(w_obs - mean)
        if dist <= 0:
            return 1.0
        top = int(score.max())
        hi = math.ceil(mean + dist - 1e-9)
        lo = math.ceil(n1 * top - mean + dist - 1e-9)
        bound = (DriftDetector._tied_chernoff(t, score, n1, hi)
                 + DriftDetector._tied_chernoff(t, top - score, n1, lo))
        if bound < _SIG_P:
            return bound
        return min(1.0, DriftDetector._tied_tail(t, score, n1, hi)
                   + DriftDetector._tied_tail(t, top - score, n1, lo))

    @staticmethod
    def _tied_chernoff(t: np.ndarray, score: np.ndarray, m: int, threshold: int) -> float:
        """An upper bound on ``_tied_tail``: W is a sample of m without replacement from
        the pooled scores, so by Hoeffding (1963, Thm. 4) its moment generating function
        is at most that of m draws with replacement, and P(W >= threshold) <= min over
        theta > 0 of exp(-theta threshold) M(theta)^m, M the pooled scores' mean
        exp(theta s)."""
        keep = t > 0
        if not keep.any():
            return 1.0
        weights = t[keep] / float(t.sum())
        s = score[keep].astype(float)
        target = threshold / m
        if target <= float((weights * s).sum()):
            return 1.0
        if target > float(s.max()):
            return 0.0
        scale = float(s.max()) or 1.0
        x = s / scale  # scores in [0, 1], so theta stays in a sane range

        def log_m(theta: float) -> float:
            e = theta * x
            top = float(e.max())
            return top + math.log(float((weights * np.exp(e - top)).sum()))

        def tilted_mean(theta: float) -> float:
            e = np.exp(theta * x - float((theta * x).max()))
            return float((weights * e * x).sum() / (weights * e).sum())

        goal = target / scale
        lo, hi = 0.0, 1.0
        while tilted_mean(hi) < goal and hi < 1e6:
            hi *= 2.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if tilted_mean(mid) < goal:
                lo = mid
            else:
                hi = mid
        return min(1.0, math.exp(m * log_m(hi) - hi * threshold / scale))

    @staticmethod
    def _tied_tail(t: np.ndarray, score: np.ndarray, m: int, threshold: int) -> float:
        """P(W >= threshold), W = sum_j x_j score_j, when m of the pooled rows (t_j of
        value j) are taken uniformly at random.

        Counted on each row's shortfall from the top score, which the tail allows
        only ``m * top - threshold`` of in all, so a clear shift costs least.
        """
        if threshold <= 0:
            return 1.0
        top = int(score.max())
        slack = m * top - threshold
        if slack < 0:
            return 0.0
        total = int(t.sum())
        log_f = math.log(m / total)
        # dp[k, e]: weight of the ways to take k rows from the values seen so far with
        # total shortfall e <= slack. Each way of taking x of value j's t_j rows weighs
        # C(t_j, x) (m / N)^x -- the factor (m / N)^m is common to every complete way,
        # and keeps the weights in range.
        dp = np.zeros((m + 1, slack + 1))
        dp[0, 0] = 1.0
        for tj, sj in zip(t.tolist(), score.tolist()):
            if tj == 0:
                continue
            short = top - int(sj)
            new = dp.copy()
            lg_t = math.lgamma(tj + 1)
            for x in range(1, min(tj, m) + 1):
                shift = x * short
                if shift > slack:
                    break
                w = math.exp(lg_t - math.lgamma(x + 1) - math.lgamma(tj - x + 1) + x * log_f)
                if shift:
                    new[x:, shift:] += w * dp[:m + 1 - x, :slack + 1 - shift]
                else:
                    new[x:] += w * dp[:m + 1 - x]
            dp = new
        log_all = math.lgamma(total + 1) - math.lgamma(m + 1) - math.lgamma(total - m + 1) + m * log_f
        return min(1.0, float(dp[m].sum()) / math.exp(log_all))

    @staticmethod
    def _rank_sum_significant(n1: int, n0: int, u_strict: int) -> bool:
        """Whether ``u_strict`` -- the pairs of (batch row, baseline row) in which the
        batch row lies in a strictly higher bin (or, for the lower tail, a strictly
        lower one) -- proves a shift at two-sided _SIG_P (BUG-579 re-review).

        Conservative for any column, binned on any edges: a pair in one bin counts
        as no evidence, so u_strict is at most the strict count of the raw values,
        which is at most the rank sum after breaking ties at random -- and that has
        the tie-free Mann-Whitney distribution under the null, whatever the shape,
        the ties, or edges taken from the baseline's own min and max. So any upper
        bound on P(U >= u_strict) under that distribution is a valid p-value, and so
        is the smaller of two. The Chernoff bound (``_mw_chernoff``) costs
        microseconds and is within a factor of a few of exact once the smaller
        sample has a dozen rows; below that it is loose (x5 at 5 rows), and the
        tail counted on a grid of score groups (``_mw_tail``) is used too, which
        there is nearly exact and still costs ~1 ms.
        """
        m, big = min(n1, n0), max(n1, n0)
        if m < 1 or u_strict <= 0:
            return False
        half = _SIG_P / 2
        if DriftDetector._mw_chernoff(m, big, u_strict) < half:
            return True
        if m > _EXACT_RANK_DP_ROWS:
            return False
        step = max(1, -(-big // max(1, _EXACT_RANK_GRID // m)))
        return DriftDetector._mw_tail(m, big, u_strict, step) < half

    @staticmethod
    def _mw_chernoff(m: int, big: int, u: int) -> float:
        """An upper bound on P(U >= u) for the tie-free Mann-Whitney U of samples of m
        and ``big`` rows (BUG-579 re-review). U + m(m+1)/2 is the rank sum of the m
        rows, a sample without replacement from the ranks 1..N; by Hoeffding (1963,
        Thm. 4) its moment generating function is at most that of m ranks drawn with
        replacement, so P(U >= u) <= min over t > 0 of exp(-t w) M(t)^m, with w the
        rank sum and M the mean of exp(t r) over r = 1..N.
        """
        n = m + big
        w = u + m * (m + 1) / 2
        target = w / m  # the mean rank the tail needs
        if target <= (n + 1) / 2:
            return 1.0
        if target >= n:  # only one row, at the top: the limit t -> infinity
            return min(1.0, float(n) ** -m)

        def log_expm1(x: float) -> float:
            return x + math.log1p(-math.exp(-x)) if x > 1.0 else math.log(math.expm1(x))

        def mean_rank(t: float) -> float:  # d/dt log M(t)
            return 1.0 + n / -math.expm1(-n * t) - 1.0 / -math.expm1(-t)

        lo, hi = 1e-12, 1.0
        while mean_rank(hi) < target:
            hi *= 2.0
        for _ in range(100):
            mid = math.sqrt(lo * hi)
            if mean_rank(mid) < target:
                lo = mid
            else:
                hi = mid
        t = hi
        log_m = t + log_expm1(n * t) - log_expm1(t) - math.log(n)
        return min(1.0, math.exp(m * log_m - t * w))

    @staticmethod
    def _mw_tail(m: int, big: int, u: int, step: int) -> float:
        """An upper bound on P(U >= u) for the tie-free Mann-Whitney U of samples of m
        and ``big`` rows (BUG-579 re-review).

        Under the null the m rows of the smaller sample fall into the big+1 gaps of
        the other uniformly over all C(big + m, m) arrangements, and U is the sum of
        their gap indices k_j. Rounding each k_j up to a multiple of ``step`` can only
        raise U, so P(sum of rounded k_j >= u) bounds P(U >= u) from above; with step
        1 it is exact. The rounded sum takes few
        values, so a dynamic program over the groups of gaps counts it: d rows in a
        group of g gaps can lie there C(d + g - 1, d) ways. It runs on each row's
        shortfall from the top group, which the tail allows only a little of, so a
        clearly shifted batch costs least.
        """
        if u <= 0:
            return 1.0
        if u > m * big:
            return 0.0
        need = -(-u // step)  # sum of group indices the tail needs
        top = -(-big // step)  # group c holds the gaps k with ceil(k / step) == c
        sizes = [1] + [step] * (top - 1) + [big - (top - 1) * step]
        slack = m * top - need  # the most shortfall the tail allows
        if slack < 0:
            return 0.0
        # dp[k, w]: weight of the ways to place k rows in the groups seen so far with
        # total shortfall w <= slack; each way of d rows among g gaps weighs
        # C(d + g - 1, d) f^d, f = m / (big + m) -- common to every complete
        # arrangement, it keeps the numbers in range.
        log_f = math.log(m / (big + m))
        dp = np.zeros((m + 1, slack + 1))
        dp[0, 0] = 1.0
        for c, g in enumerate(sizes):
            short = top - c
            new = dp.copy()
            lg = math.lgamma(g)
            for d in range(1, m + 1):
                shift = d * short
                if shift > slack:
                    break
                w = math.exp(math.lgamma(d + g) - math.lgamma(d + 1) - lg + d * log_f)
                if shift:
                    new[d:, shift:] += w * dp[:m + 1 - d, :slack + 1 - shift]
                else:
                    new[d:] += w * dp[:m + 1 - d]
            dp = new
        log_total = math.lgamma(big + m + 1) - math.lgamma(m + 1) - math.lgamma(big + 1) + m * log_f
        return min(1.0, float(dp[m].sum()) / math.exp(log_total))

    @staticmethod
    def _plugin_kl(p: np.ndarray, q: np.ndarray) -> float:
        nz = p > 0
        return max(float(np.sum(p[nz] * np.log(p[nz] / q[nz]))), 0.0)

    # ────────────────────────────────────────────────────────────────
    # Batch embedding (hash-projection, no external API needed)
    # ────────────────────────────────────────────────────────────────

    def _categorical_columns(self, batch: BatchPayload) -> List[str]:
        """Return columns whose values are *not* fully numeric.

        A column counts as categorical/textual when at least one non-null
        value fails to parse as ``float``.  Fully-numeric columns are
        excluded from the semantic embedding (see ``_compute_batch_embedding``)
        because hashing ``col:value`` for continuous data yields a unique
        token per row.  This mirrors the numeric-vs-categorical split used by
        ``_compute_distributions`` so the two channels agree on column type.
        """
        if not batch.rows:
            return []
        columns = batch.columns or list(batch.rows[0].keys())
        categorical: List[str] = []
        for col in columns:
            saw_value = False
            is_numeric = True
            for row in batch.rows:
                val = row.get(col)
                if val is None:
                    continue
                saw_value = True
                try:
                    float(val)
                except (ValueError, TypeError):
                    is_numeric = False
                    break
            if saw_value and not is_numeric:
                categorical.append(col)
        return categorical

    def _compute_batch_embedding(
        self, batch: BatchPayload, dim: int = 256, exclude: Optional[set] = None,
    ) -> Optional[List[float]]:
        """
        Compute a lightweight embedding for the entire batch using
        feature hashing (random projection). No external model needed.

        Only *categorical / textual* columns contribute to the embedding.
        Continuous numeric columns are excluded: hashing ``col:value`` for
        floating-point data yields a unique token per row, so any two batches
        share almost no dimensions and the cosine distance is ~1.0 regardless
        of whether the underlying distribution actually drifted.  Feeding such
        columns to the semantic channel produced constant false-positive
        SEMANTIC/CRITICAL alarms on healthy numeric streams.  When a batch has
        no categorical columns this returns ``None`` and the caller skips the
        semantic channel entirely.
        """
        if not batch.rows:
            return None

        categorical_cols = [c for c in self._categorical_columns(batch) if c not in (exclude or ())]
        if not categorical_cols:
            return None

        vector = [0.0] * dim
        row_count = 0

        for row in batch.rows:
            for col in categorical_cols:
                val = row.get(col)
                if val is None:
                    continue
                # Hash column+value to get a dimension index. BUG-069: must be
                # a STABLE hash -- Python's builtin hash() is randomized per
                # process (PEP 456 / PYTHONHASHSEED) unless explicitly
                # disabled, so the same token maps to a different dimension
                # in every process. A baseline embedding registered by one
                # process (persisted via RedisStateStore, explicitly meant to
                # be shared across worker replicas / survive a restart) then
                # shares almost no dimensions with a batch embedded by a
                # different process, making cosine distance ~1.0 regardless
                # of whether the data actually drifted -- silently defeating
                # the semantic-drift channel. zlib.crc32 is deterministic
                # across processes/restarts. It's unsigned, so the sign bit
                # (needed for the random-projection/SimHash trick) is derived
                # from a low bit of the same hash rather than from crc32's
                # sign (which doesn't exist), and the index uses the
                # remaining bits so the two don't reuse the same bit.
                token = f"{col}:{val}"
                h = zlib.crc32(token.encode("utf-8"))
                idx = (h >> 1) % dim
                sign = 1.0 if (h & 1) else -1.0
                vector[idx] += sign
                row_count += 1

        # Normalize
        if row_count > 0:
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vector = [x / norm for x in vector]
        else:
            return None

        return vector

    # ────────────────────────────────────────────────────────────────
    # Cosine distance
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_distance(a: List[float], b: List[float]) -> float:
        """1 - cosine_similarity(a, b)."""
        if len(a) != len(b):
            return 1.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
        norm_b = math.sqrt(sum(x * x for x in b)) or 1.0

        similarity = dot / (norm_a * norm_b)
        return 1.0 - similarity

    # ────────────────────────────────────────────────────────────────
    # Utility — snapshot current distributions for persistence
    # ────────────────────────────────────────────────────────────────

    def snapshot_distributions(
        self, batch: BatchPayload
    ) -> Dict[str, ColumnDistribution]:
        """Compute & return distributions without checking drift. Useful for initial baselining."""
        return self._compute_distributions(batch)
