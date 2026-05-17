"""
FAISS connector tests — Sprint 17 (Multi-Modal Fabric, Pillar 2).

Anchors:
  * Johnson, J., Douze, M. & Jegou, H. (2019). "Billion-scale similarity
    search with GPUs." IEEE Transactions on Big Data 7(3):535-547.

Covers:

* The FAISS connector imports cleanly and reports its capabilities.
* ConnectorSpec for "faiss" is registered with available=True when
  faiss-cpu is installed.
* connect() builds an empty FlatIP index of the configured dimension.
* add_vectors stores vectors + metadata; vector_search returns the
  expected nearest neighbours with stable ranking.
* HNSW index type works as an alternative.
* Dimension mismatch is caught (returns empty result, does not raise).
* Persistence: an index written to disk reloads with the same vectors.

Tests skip with a clear message when faiss-cpu isn't installed, so the
backend-test CI lane (which doesn't install requirements-multimodal.txt)
sees skipped tests instead of import errors.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

faiss = pytest.importorskip(
    "faiss",
    reason="faiss-cpu is an optional dep — install requirements-multimodal.txt",
)

from connectors import ConnectorConfig, FAISSConnector, SourceType, available_connectors

# ── Registry surface ──────────────────────────────────────────────────

def test_faiss_connector_spec_registered():
    """The 'faiss' connector ID appears in the registry with the
    capabilities the spec declares."""
    specs = {s.id: s for s in available_connectors()}
    assert "faiss" in specs, f"faiss not registered. Got: {list(specs.keys())}"
    spec = specs["faiss"]
    assert spec.available is True, (
        f"faiss should be available when faiss-cpu is installed; "
        f"unavailable_reason={spec.unavailable_reason}"
    )
    assert "vector" in spec.capabilities
    assert spec.kind == "embedded"


def test_faiss_source_type_enum_present():
    assert SourceType.FAISS.value == "faiss"


# ── Lifecycle + capabilities ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_faiss_connector_connect_empty_index():
    """Fresh in-memory connector connects, reports vector capability."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_empty",
        extra_params={"dimension": 16},
    )
    c = FAISSConnector(cfg)
    ok = await c.connect()
    assert ok is True
    assert c.is_connected() is True

    caps = c.capabilities()
    assert caps == {"sql": False, "vector": True, "spatial": False}

    # Empty index — list_tables returns the single logical table name
    tables = await c.list_tables()
    assert tables == [FAISSConnector.DEFAULT_TABLE_NAME]

    profile = await c.profile_table(FAISSConnector.DEFAULT_TABLE_NAME)
    assert profile["n_vectors"] == 0
    assert profile["dimension"] == 16

    await c.disconnect()


# ── add_vectors + vector_search ───────────────────────────────────────

@pytest.mark.asyncio
async def test_faiss_add_vectors_and_search_recovers_nearest():
    """Add 5 known vectors, query with one of them, verify the matching
    row is rank 0. This is the basic correctness contract.

    For IndexFlatIP (inner product) with non-normalised vectors the
    distance is the dot product; the highest-dot-product row should
    rank first. We pick distinct rows so there's no ambiguity.
    """
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_search",
        extra_params={"dimension": 4},
    )
    c = FAISSConnector(cfg)
    assert await c.connect()

    rows = [
        ([1.0, 0.0, 0.0, 0.0], {"label": "x-axis"}),
        ([0.0, 1.0, 0.0, 0.0], {"label": "y-axis"}),
        ([0.0, 0.0, 1.0, 0.0], {"label": "z-axis"}),
        ([0.0, 0.0, 0.0, 1.0], {"label": "w-axis"}),
        ([0.5, 0.5, 0.0, 0.0], {"label": "xy-diagonal"}),
    ]
    vectors = [v for v, _ in rows]
    metadata = [m for _, m in rows]
    start_id = await c.add_vectors(vectors, metadata)
    assert start_id == 0

    # Query with the x-axis vector — top hit should be "x-axis"
    hits = await c.vector_search(
        FAISSConnector.DEFAULT_TABLE_NAME,
        embedding=[1.0, 0.0, 0.0, 0.0],
        limit=3,
    )
    assert len(hits) == 3
    assert hits[0]["label"] == "x-axis"
    # xy-diagonal has dot 0.5 with the query, second-best
    assert hits[1]["label"] == "xy-diagonal"
    # All hits carry the _rank + _distance fields the contract promises
    for i, hit in enumerate(hits):
        assert hit["_rank"] == i
        assert "_distance" in hit
        assert "id" in hit


@pytest.mark.asyncio
async def test_faiss_dimension_mismatch_returns_empty_not_raise():
    """Querying with the wrong-dimension vector must NOT raise — the
    BaseConnector contract is structured failure, not exceptions."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_dim_mismatch",
        extra_params={"dimension": 4},
    )
    c = FAISSConnector(cfg)
    await c.connect()
    await c.add_vectors([[1.0, 0.0, 0.0, 0.0]])

    # Query with a 5-D vector against a 4-D index
    hits = await c.vector_search(
        FAISSConnector.DEFAULT_TABLE_NAME,
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0],
    )
    assert hits == []


@pytest.mark.asyncio
async def test_faiss_search_empty_index_returns_empty():
    """Search against an empty index returns an empty list, not None
    and not a crash."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_empty_search",
        extra_params={"dimension": 4},
    )
    c = FAISSConnector(cfg)
    await c.connect()
    hits = await c.vector_search(
        FAISSConnector.DEFAULT_TABLE_NAME,
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    assert hits == []


@pytest.mark.asyncio
async def test_faiss_add_vectors_rejects_wrong_dimension():
    """add_vectors with the wrong dimension is a programming error;
    we DO raise here (unlike search) because the caller had to
    construct the vectors deliberately."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_add_dim",
        extra_params={"dimension": 4},
    )
    c = FAISSConnector(cfg)
    await c.connect()
    with pytest.raises(ValueError, match="shape"):
        await c.add_vectors([[1.0, 0.0, 0.0]])  # 3-D into 4-D index


# ── HNSW index variant ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_faiss_hnsw_index_works():
    """HNSW path connects + adds + searches. We don't assert exact
    ranking because HNSW is approximate by design."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_hnsw",
        extra_params={"dimension": 8, "index_type": "hnsw", "hnsw_m": 16},
    )
    c = FAISSConnector(cfg)
    assert await c.connect()
    # 20 random vectors — HNSW likes a few rows before search is meaningful
    rng = np.random.default_rng(42)
    vectors = rng.standard_normal((20, 8)).astype(np.float32).tolist()
    await c.add_vectors(vectors, metadata=[{"i": i} for i in range(20)])

    hits = await c.vector_search(
        FAISSConnector.DEFAULT_TABLE_NAME,
        embedding=vectors[5],
        limit=3,
    )
    # HNSW should still rank the query itself first or very near first
    top_ids = [h["i"] for h in hits]
    assert 5 in top_ids


# ── Persistence ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_faiss_persistence_round_trip(tmp_path):
    """Index + metadata persist to disk and reload with the same
    vectors. Auditors expect write durability without a separate
    flush call — add_vectors persists immediately when a path is
    configured."""
    index_path = str(tmp_path / "faiss.index")
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_persist",
        connection_string=index_path,
        extra_params={"dimension": 4},
    )
    c1 = FAISSConnector(cfg)
    await c1.connect()
    await c1.add_vectors(
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        metadata=[{"label": "x"}, {"label": "y"}],
    )
    await c1.disconnect()

    # Files were written
    assert os.path.exists(index_path)
    assert os.path.exists(index_path + ".meta.parquet")

    # Reload in a fresh connector — same dim, same path
    cfg2 = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_persist_reload",
        connection_string=index_path,
        extra_params={"dimension": 4},
    )
    c2 = FAISSConnector(cfg2)
    await c2.connect()
    profile = await c2.profile_table(FAISSConnector.DEFAULT_TABLE_NAME)
    assert profile["n_vectors"] == 2

    hits = await c2.vector_search(
        FAISSConnector.DEFAULT_TABLE_NAME,
        embedding=[1.0, 0.0, 0.0, 0.0],
        limit=2,
    )
    assert hits[0]["label"] == "x"


# ── execute_query fallback ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_faiss_execute_query_returns_metadata_sample():
    """execute_query has no SQL surface; it returns the metadata
    sample so callers using the generic connector interface (e.g.
    the agent layer) get SOMETHING rather than an error."""
    cfg = ConnectorConfig(
        source_type=SourceType.FAISS,
        name="test_exec",
        extra_params={"dimension": 4},
    )
    c = FAISSConnector(cfg)
    await c.connect()
    await c.add_vectors([[1.0, 0.0, 0.0, 0.0]], metadata=[{"label": "only"}])
    rows = await c.execute_query("SELECT * FROM whatever LIMIT 1")
    assert len(rows) == 1
    assert rows[0]["label"] == "only"
