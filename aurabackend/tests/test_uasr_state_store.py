"""
Tests for the UASR per-source StateStore and its integration with the
DriftDetector (Bottleneck #3: horizontal state externalization).

Covers:
  * SourceState JSON round-trip (the RedisStateStore wire format).
  * InMemoryStateStore load/save/peek/LRU/parity semantics.
  * DriftDetector wired onto a store: default parity, LRU bound (fixes
    unbounded O(n_sources) growth), and shared-store cold-miss fix
    (a baseline registered on one replica is visible to another).
  * A fake Redis client proving RedisStateStore shares state across
    detectors without a live server.
"""
from __future__ import annotations

import random

import pytest

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, ColumnDistribution
from uasr.state_store import (
    InMemoryStateStore,
    RedisStateStore,
    SourceState,
)


def _mkbatch(source_id: str, mu: float = 0.0, n: int = 200, seed: int = 1) -> BatchPayload:
    random.seed(seed)
    return BatchPayload(
        source_id=source_id,
        batch_id="b",
        rows=[{"value": random.gauss(mu, 1.0)} for _ in range(n)],
    )


# ────────────────────────────────────────────────────────────────────
# SourceState serialization
# ────────────────────────────────────────────────────────────────────
class TestSourceStateSerialization:
    def test_empty_roundtrip(self):
        st = SourceState()
        assert st.is_empty()
        back = SourceState.from_json(st.to_json())
        assert back.is_empty()
        assert back.baseline is None and back.schema is None
        assert back.kl_history == [] and back.embeddings == []

    def test_full_roundtrip_preserves_baseline(self):
        det = DriftDetector()
        det.register_baseline("s", _mkbatch("s"))
        st = det._store.peek("s")
        st.schema = {"value": "float"}
        st.kl_history = [0.1, 0.2, 0.3]
        st.embeddings = [[0.0] * 4, [1.0] * 4]

        back = SourceState.from_json(st.to_json())
        assert set(back.baseline.keys()) == set(st.baseline.keys())
        # ColumnDistribution reconstructed with identical stats
        for col, dist in st.baseline.items():
            assert back.baseline[col].mean == pytest.approx(dist.mean)
            assert back.baseline[col].std == pytest.approx(dist.std)
            assert back.baseline[col].distinct_count == dist.distinct_count
        assert back.schema == {"value": "float"}
        assert back.kl_history == [0.1, 0.2, 0.3]
        assert back.embeddings == [[0.0] * 4, [1.0] * 4]


# ────────────────────────────────────────────────────────────────────
# InMemoryStateStore semantics
# ────────────────────────────────────────────────────────────────────
class TestInMemoryStateStore:
    def test_load_absent_returns_empty_without_inserting(self):
        store = InMemoryStateStore()
        st = store.load("nope")
        assert st.is_empty()
        # dict.get semantics: absent load must NOT create a record
        assert not store.contains("nope")
        assert len(store) == 0

    def test_save_then_load(self):
        store = InMemoryStateStore()
        st = SourceState(schema={"a": "int"})
        store.save("s", st)
        assert store.contains("s")
        assert store.load("s").schema == {"a": "int"}

    def test_capacity_must_be_positive(self):
        with pytest.raises(ValueError):
            InMemoryStateStore(capacity=0)

    def test_lru_evicts_coldest(self):
        store = InMemoryStateStore(capacity=2)
        store.save("a", SourceState(schema={"x": "int"}))
        store.save("b", SourceState(schema={"x": "int"}))
        # touch "a" so "b" becomes coldest
        store.load("a")
        store.save("c", SourceState(schema={"x": "int"}))
        assert len(store) == 2
        assert store.contains("a") and store.contains("c")
        assert not store.contains("b")
        assert store.evicted == ["b"]

    def test_peek_does_not_refresh_recency(self):
        store = InMemoryStateStore(capacity=2)
        store.save("a", SourceState(schema={"x": "int"}))
        store.save("b", SourceState(schema={"x": "int"}))
        # peek "a" — must NOT protect it from eviction
        store.peek("a")
        store.save("c", SourceState(schema={"x": "int"}))
        # "a" was least-recently *saved/loaded*; peek didn't refresh it
        assert not store.contains("a")
        assert store.contains("b") and store.contains("c")


# ────────────────────────────────────────────────────────────────────
# DriftDetector integration: parity + the two failure-mode fixes
# ────────────────────────────────────────────────────────────────────
class TestDetectorStateStoreIntegration:
    def test_default_store_is_used_not_discarded(self):
        # regression: empty InMemoryStateStore is falsy (defines __len__);
        # `state_store or InMemoryStateStore()` would discard it.
        store = InMemoryStateStore(capacity=5)
        det = DriftDetector(state_store=store)
        assert det._store is store

    def test_default_unbounded_parity(self):
        det = DriftDetector()
        for i in range(300):
            det.register_baseline(f"s{i}", _mkbatch(f"s{i}"))
        # unbounded default never evicts — bit-identical to old dicts
        assert len(det._store) == 300
        assert "s0" in det._baselines and "s299" in det._baselines

    def test_lru_bounds_memory(self):
        store = InMemoryStateStore(capacity=100)
        det = DriftDetector(state_store=store)
        for i in range(2000):
            det.register_baseline(f"src_{i}", _mkbatch(f"src_{i}"))
        assert len(store) == 100
        assert len(store.evicted) == 1900

    def test_shared_store_fixes_cross_replica_cold_miss(self):
        shared = InMemoryStateStore()
        replica_a = DriftDetector(state_store=shared)
        replica_b = DriftDetector(state_store=shared)
        replica_a.register_baseline("orders", _mkbatch("orders", mu=0.0))
        # batch rebalanced to replica B still detects the shift
        res = replica_b.detect(_mkbatch("orders", mu=3.0))
        assert "orders" in replica_b._baselines
        assert res.drift_detected is True

    def test_separate_default_stores_stay_isolated(self):
        d1, d2 = DriftDetector(), DriftDetector()
        d1.register_baseline("orders", _mkbatch("orders"))
        assert "orders" not in d2._baselines

    def test_back_compat_views_reflect_store(self):
        det = DriftDetector()
        det.register_baseline("s", _mkbatch("s"), schema={"value": "float"})
        det.register_reference_embedding("s", [0.1] * 8)
        assert "s" in det._baselines
        assert det._schema_baselines["s"] == {"value": "float"}
        assert det._reference_embeddings["s"] == [[0.1] * 8]

    def test_kl_history_accumulates_via_store(self):
        det = DriftDetector()
        det.register_baseline("s", _mkbatch("s", mu=0.0))
        for _ in range(6):
            det.detect(_mkbatch("s", mu=0.0))
        # statistical check appends avg KL each batch with rows+baseline
        assert len(det._kl_history.get("s", [])) >= 1


# ────────────────────────────────────────────────────────────────────
# RedisStateStore with a fake client (no live server)
# ────────────────────────────────────────────────────────────────────
class _FakeRedis:
    """Minimal in-process stand-in for the redis client surface used."""

    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def exists(self, k):
        return 1 if k in self.kv else 0

    def delete(self, k):
        self.kv.pop(k, None)

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.kv if k.startswith(prefix)]


class TestRedisStateStore:
    def test_roundtrip_via_fake_client(self):
        store = RedisStateStore(client=_FakeRedis())
        st = SourceState(schema={"value": "float"}, kl_history=[0.1, 0.2])
        store.save("s", st)
        assert store.contains("s")
        back = store.load("s")
        assert back.schema == {"value": "float"}
        assert back.kl_history == [0.1, 0.2]
        assert store.source_ids() == ["s"]

    def test_shared_across_detectors(self):
        shared = RedisStateStore(client=_FakeRedis())
        a = DriftDetector(state_store=shared)
        b = DriftDetector(state_store=shared)
        a.register_baseline("orders", _mkbatch("orders", mu=0.0))
        res = b.detect(_mkbatch("orders", mu=3.0))
        assert res.drift_detected is True


# ────────────────────────────────────────────────────────────────────
# RedisStateStore against real Redis command semantics (fakeredis)
# ────────────────────────────────────────────────────────────────────
# The _FakeRedis stand-in above proves RedisStateStore calls the right
# method *names*.  These tests run the store against ``fakeredis``, a
# high-fidelity in-process Redis that implements actual command
# semantics — bytes-valued GET returns, real TTL/PTTL expiry, and glob
# KEYS matching — so the JSON wire format and key handling are exercised
# the way a live server would exercise them.  Skips cleanly if fakeredis
# is not installed (keeps CI green without the optional dep).
class TestRedisStateStoreIntegration:
    def _client(self):
        fakeredis = pytest.importorskip("fakeredis")
        return fakeredis.FakeStrictRedis()

    def _rich_state(self):
        cd = ColumnDistribution(
            column_name="value", mean=3.14, std=1.2, null_rate=0.01,
            distinct_count=42, sample_size=200,
            histogram={"bins": [1, 2, 3], "counts": [10, 20, 30]},
        )
        return cd, SourceState(
            baseline={"value": cd}, schema={"value": "float"},
            kl_history=[0.1, 0.2, 0.3], embeddings=[[0.5, 0.6], [0.7, 0.8]],
        )

    def test_get_returns_bytes_and_full_roundtrip(self):
        r = self._client()
        store = RedisStateStore(client=r)
        _, st = self._rich_state()
        store.save("srcA", st)
        # Real redis GET returns bytes; the store must decode + rebuild.
        assert isinstance(r.get("uasr:state:srcA"), bytes)
        back = store.load("srcA")
        assert back.baseline["value"].mean == 3.14
        assert back.baseline["value"].histogram == {"bins": [1, 2, 3], "counts": [10, 20, 30]}
        assert back.schema == {"value": "float"}
        assert back.kl_history == [0.1, 0.2, 0.3]
        assert back.embeddings == [[0.5, 0.6], [0.7, 0.8]]

    def test_contains_delete_and_empty_on_miss(self):
        store = RedisStateStore(client=self._client())
        _, st = self._rich_state()
        store.save("srcA", st)
        assert store.contains("srcA") is True
        assert store.contains("absent") is False
        store.delete("srcA")
        assert store.contains("srcA") is False
        assert store.load("srcA").is_empty()

    def test_source_ids_via_real_keys_glob(self):
        store = RedisStateStore(client=self._client())
        for sid in ("p1", "p2", "p3"):
            store.save(sid, SourceState(schema={"c": "int"}))
        assert sorted(store.source_ids()) == ["p1", "p2", "p3"]

    def test_ttl_is_actually_applied(self):
        r = self._client()
        store = RedisStateStore(client=r, ttl_seconds=100)
        store.save("ephemeral", SourceState(schema={"x": "int"}))
        pttl = r.pttl("uasr:state:ephemeral")
        # PTTL in ms; must be a positive value bounded by the configured TTL.
        assert 0 < pttl <= 100_000

    def test_cross_replica_cold_miss_fix(self):
        # Two stores share one backend = two worker replicas on one Redis.
        r = self._client()
        cd, _ = self._rich_state()
        replica1 = RedisStateStore(client=r)
        replica2 = RedisStateStore(client=r)
        replica1.save("shared_src", SourceState(baseline={"v": cd}))
        seen = replica2.load("shared_src")
        assert seen.baseline is not None
        assert seen.baseline["v"].mean == 3.14

    def test_detectors_share_state_over_real_redis(self):
        shared = RedisStateStore(client=self._client())
        a = DriftDetector(state_store=shared)
        b = DriftDetector(state_store=shared)
        a.register_baseline("orders", _mkbatch("orders", mu=0.0))
        # b never saw the baseline in-process; it must read it from Redis.
        res = b.detect(_mkbatch("orders", mu=3.0))
        assert res.drift_detected is True
