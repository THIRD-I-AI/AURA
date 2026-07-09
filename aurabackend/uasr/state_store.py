"""
UASR Per-Source State Store
===========================
Externalises the drift detector's per-source state so the self-healing
loop can scale **horizontally** across many pipelines and worker replicas.

Motivation
----------
``DriftDetector`` originally held four in-process dictionaries keyed by
``source_id`` (baselines, KL history, schema baselines, reference
embeddings).  That design has two hard limits at enterprise scale:

1. **Unbounded growth** — the dicts are never evicted, so resident memory
   is ``O(n_sources)`` and grows without bound as pipelines are added.
2. **Cross-replica cold-miss** — the state lives in one process's memory,
   so a batch that Kafka rebalances to a *different* worker replica finds
   no baseline and detection goes blind.

This module introduces a small ``StateStore`` abstraction with three
backends:

* :class:`InMemoryStateStore` (default, ``capacity=None``) — behaviour is
  **bit-identical** to the original loose-dict design.
* :class:`InMemoryStateStore` (``capacity=N``) — LRU-bounded; evicts the
  coldest source's *entire* state atomically, capping memory at O(N).
* :class:`RedisStateStore` — shared across replicas; a baseline registered
  on any worker is visible to all, resolving the cold-miss.

The eviction / sharing unit is the **source**: a source's baseline, KL
history, schema baseline and reference embeddings live and die together in
one :class:`SourceState` record.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import ColumnDistribution


# ────────────────────────────────────────────────────────────────────
# Per-source state record
# ────────────────────────────────────────────────────────────────────
@dataclass
class SourceState:
    """All per-source drift state, grouped so it evicts/shares atomically."""

    baseline: Optional[Dict[str, ColumnDistribution]] = None
    schema: Optional[Dict[str, str]] = None
    kl_history: List[float] = field(default_factory=list)
    embeddings: List[List[float]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            self.baseline is None
            and self.schema is None
            and not self.kl_history
            and not self.embeddings
        )

    # ---- serialization (used by RedisStateStore / any wire backend) ----
    def to_json(self) -> str:
        return json.dumps(
            {
                "baseline": (
                    {k: v.model_dump() for k, v in self.baseline.items()}
                    if self.baseline is not None
                    else None
                ),
                "schema": self.schema,
                "kl_history": self.kl_history,
                "embeddings": self.embeddings,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "SourceState":
        d = json.loads(raw)
        baseline = d.get("baseline")
        return cls(
            baseline=(
                {k: ColumnDistribution(**v) for k, v in baseline.items()}
                if baseline is not None
                else None
            ),
            schema=d.get("schema"),
            kl_history=list(d.get("kl_history") or []),
            embeddings=[list(e) for e in (d.get("embeddings") or [])],
        )


# ────────────────────────────────────────────────────────────────────
# Store interface
# ────────────────────────────────────────────────────────────────────
class StateStore(ABC):
    """Load / save / evict per-source drift state, keyed by ``source_id``."""

    @abstractmethod
    def load(self, source_id: str) -> SourceState:
        """Return the source's state, or a fresh empty ``SourceState``.

        For LRU backends this counts as an access and refreshes recency.
        The returned object is safe to mutate; persist changes via
        :meth:`save`.
        """

    @abstractmethod
    def save(self, source_id: str, state: SourceState) -> None:
        """Persist (and, for LRU backends, mark most-recently-used)."""

    def peek(self, source_id: str) -> SourceState:
        """Like :meth:`load` but MUST NOT refresh LRU recency.

        Used by read-only introspection (health endpoints, back-compat
        views) that should not perturb eviction order.  Default delegates
        to :meth:`load`; LRU backends override.
        """
        return self.load(source_id)

    @abstractmethod
    def contains(self, source_id: str) -> bool:
        """True if a state record exists (does *not* refresh recency)."""

    @abstractmethod
    def delete(self, source_id: str) -> None:
        ...

    @abstractmethod
    def source_ids(self) -> List[str]:
        ...

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.source_ids())


# ────────────────────────────────────────────────────────────────────
# In-memory backend (default) — optional LRU capacity
# ────────────────────────────────────────────────────────────────────
class InMemoryStateStore(StateStore):
    """Process-local state store.

    * ``capacity=None`` (default): unbounded — **bit-identical** to the
      original loose-dict behaviour.
    * ``capacity=N``: LRU — when a save pushes the population past ``N``,
      the least-recently-used source's entire state is evicted.  Eviction
      IDs are recorded on :attr:`evicted` for observability / tests.
    """

    def __init__(self, capacity: Optional[int] = None) -> None:
        if capacity is not None and capacity < 1:
            raise ValueError(f"capacity must be >= 1 or None; got {capacity}")
        self._capacity = capacity
        self._data: "OrderedDict[str, SourceState]" = OrderedDict()
        self.evicted: List[str] = []

    def load(self, source_id: str) -> SourceState:
        st = self._data.get(source_id)
        if st is None:
            # Absent: return an empty record but do NOT insert yet — a
            # record only occupies capacity once it holds real state and
            # is saved.  (Matches the original ``dict.get`` semantics.)
            return SourceState()
        self._data.move_to_end(source_id)  # mark most-recently-used
        return st

    def peek(self, source_id: str) -> SourceState:
        st = self._data.get(source_id)
        return st if st is not None else SourceState()

    def save(self, source_id: str, state: SourceState) -> None:
        self._data[source_id] = state
        self._data.move_to_end(source_id)
        self._evict_if_needed()

    def contains(self, source_id: str) -> bool:
        return source_id in self._data

    def delete(self, source_id: str) -> None:
        self._data.pop(source_id, None)

    def source_ids(self) -> List[str]:
        return list(self._data.keys())

    def _evict_if_needed(self) -> None:
        if self._capacity is None:
            return
        while len(self._data) > self._capacity:
            evicted_id, _ = self._data.popitem(last=False)  # oldest
            self.evicted.append(evicted_id)


# ────────────────────────────────────────────────────────────────────
# Redis backend — shared across worker replicas
# ────────────────────────────────────────────────────────────────────
class RedisStateStore(StateStore):
    """Shared state store backed by Redis.

    State is serialised to JSON under ``{prefix}{source_id}``.  Because the
    store is shared, a baseline registered on *any* replica is immediately
    visible to all others — resolving the cross-replica cold-miss.

    Memory is bounded by an optional per-key ``ttl_seconds`` (idle sources
    expire) and/or a Redis ``maxmemory`` + ``allkeys-lru`` server policy.

    ``redis`` is an optional dependency (imported lazily), mirroring the
    duckdb / kafka optional deps in ``mapek_worker``.  A client may be
    injected (``client=``) for testing without a live server.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        prefix: str = "uasr:state:",
        ttl_seconds: Optional[int] = None,
        client: object = None,
    ) -> None:
        self._prefix = prefix
        self._ttl = ttl_seconds
        if client is not None:
            self._r = client
        else:  # pragma: no cover - requires a live redis server
            try:
                import redis  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "RedisStateStore requires the 'redis' package. "
                    "Install it (pip install redis) or use InMemoryStateStore."
                ) from exc
            self._r = redis.Redis.from_url(url)

    def _key(self, source_id: str) -> str:
        return f"{self._prefix}{source_id}"

    def load(self, source_id: str) -> SourceState:
        raw = self._r.get(self._key(source_id))
        if raw is None:
            return SourceState()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return SourceState.from_json(raw)

    def save(self, source_id: str, state: SourceState) -> None:
        payload = state.to_json()
        if self._ttl is not None:
            self._r.set(self._key(source_id), payload, ex=self._ttl)
        else:
            self._r.set(self._key(source_id), payload)

    def contains(self, source_id: str) -> bool:
        return bool(self._r.exists(self._key(source_id)))

    def delete(self, source_id: str) -> None:
        self._r.delete(self._key(source_id))

    def source_ids(self) -> List[str]:
        keys = self._r.keys(f"{self._prefix}*")
        out = []
        for k in keys:
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            out.append(k[len(self._prefix):])
        return out
