"""
FAISS in-process vector connector for AURA — Sprint 17 (Multi-Modal
Fabric, Pillar 2).

Why a first-class FAISS connector when Postgres+pgvector already works
via the existing PostgreSQL connector?

Two reasons:

  1. **Zero external dependency.** FAISS is a single Python wheel
     (``faiss-cpu``). No Postgres server, no extension to install on
     the cluster, no network round-trip. The minimum viable
     vector-similarity surface for an AURA deployment is "import
     faiss + register the connector" — one command.

  2. **In-process state for the audit engine.** Sprint 17 lays the
     groundwork for cross-connector counterfactual queries (see
     ``STREAMING_FOUNDATIONS.md`` and the S17 spec in the plan
     file). When a counterfactual asks "what if we'd offered the
     program to customers semantically similar to row X within Y km
     of the new store?", the vector half needs to be cheap to
     re-run during conformal calibration's resampling loop. A local
     FAISS index makes that O(1ms per nearest-neighbour query);
     network-backed Pinecone or pgvector add 10-50ms each.

Storage model:

  * Each registered FAISS connector owns ONE in-process index.
  * The index is fronted by a metadata pandas DataFrame so each row
    has a stable integer ID, the embedding, and arbitrary other
    columns (which agents can join against).
  * Persistence is via ``index.write_index()`` / ``read_index()`` to
    the path in ``ConnectorConfig.connection_string``; if the path
    doesn't exist on connect, the connector starts with an empty
    index and persists on the first ``add_vectors`` call.
  * The ``connect()`` lifecycle uses an ``IndexFlatIP`` (inner
    product) by default — works for normalised embeddings and gives
    exact cosine similarity. Callers can override to ``IndexHNSWFlat``
    for approximate but faster lookup on larger datasets by passing
    ``extra_params={"index_type": "hnsw", "hnsw_m": 32}``.

The connector reuses the engine's ``CounterfactualEstimate``-style
contract: failures are returned as structured records (empty results
+ logged warning), never raised. The orchestrator can compose vector
+ spatial + relational queries without worrying about a single
backend's transient failure breaking the whole DAG.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from .base import BaseConnector, ConnectorConfig, SourceType

logger = logging.getLogger("aura.connectors.faiss")


class FAISSConnector(BaseConnector):
    """In-process FAISS vector store with a sidecar metadata frame.

    The connector wraps one FAISS index + one pandas DataFrame in
    parallel: row ``i`` of the DataFrame corresponds to integer ID
    ``i`` in the FAISS index. ``add_vectors`` appends to both;
    ``vector_search`` returns the matching DataFrame rows with a
    ``_distance`` column added.

    The single-table model is intentional. FAISS doesn't have a
    relational concept; the "table" is just the index. Listing
    tables returns the connector's name. Multiple logical vector
    stores = multiple registered connectors.
    """

    DEFAULT_TABLE_NAME = "embeddings"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._index: Any = None
        self._metadata: Any = None        # pandas DataFrame
        self._dim: Optional[int] = None
        self._lock = threading.Lock()
        self._index_path: Optional[str] = None

    async def connect(self) -> bool:
        try:
            import faiss  # noqa: F401  — driver presence check
            import numpy as np  # noqa: F401
            import pandas as pd  # noqa: F401
        except ImportError as exc:
            logger.warning("FAISSConnector: missing optional dep (%s)", exc)
            return False

        params = self.config.extra_params or {}
        self._dim = int(params.get("dimension", 384))
        self._index_path = self.config.connection_string or None
        # Build / load the index
        try:
            await self._build_or_load_index()
            self._is_connected = True
            self.metadata.connected = True
            return True
        except Exception as exc:
            logger.warning("FAISSConnector connect failed: %s", exc)
            return False

    async def _build_or_load_index(self) -> None:
        """Build a fresh empty index OR load from ``self._index_path``."""
        import faiss  # type: ignore[import-untyped]
        import pandas as pd

        params = self.config.extra_params or {}
        index_type = params.get("index_type", "flat_ip")

        if self._index_path and os.path.exists(self._index_path):
            # Load persisted index + metadata sidecar
            self._index = faiss.read_index(self._index_path)
            meta_path = self._index_path + ".meta.parquet"
            if os.path.exists(meta_path):
                self._metadata = pd.read_parquet(meta_path)
            else:
                # Index without metadata — create an empty frame with
                # just an ``id`` column so search results have something
                # to join against.
                self._metadata = pd.DataFrame(
                    {"id": list(range(self._index.ntotal))},
                )
            logger.info(
                "FAISSConnector loaded %d vectors from %s",
                self._index.ntotal, self._index_path,
            )
            return

        # Fresh index
        if index_type == "hnsw":
            m = int(params.get("hnsw_m", 32))
            self._index = faiss.IndexHNSWFlat(self._dim, m)
        elif index_type == "l2":
            self._index = faiss.IndexFlatL2(self._dim)
        else:
            # Default: inner-product (cosine when vectors are normalised)
            self._index = faiss.IndexFlatIP(self._dim)
        self._metadata = pd.DataFrame()
        logger.info(
            "FAISSConnector created empty %s index (dim=%d)",
            index_type, self._dim,
        )

    async def disconnect(self) -> bool:
        """Persist the index (if a path is configured) and clear state."""
        if self._index is not None and self._index_path:
            try:
                await self._persist()
            except Exception as exc:
                logger.warning("FAISSConnector persist on disconnect failed: %s", exc)
        self._index = None
        self._metadata = None
        self._is_connected = False
        self.metadata.connected = False
        return True

    async def _persist(self) -> None:
        """Write the index + metadata to disk at ``self._index_path``."""
        import faiss

        if not self._index_path:
            return
        faiss.write_index(self._index, self._index_path)
        if self._metadata is not None and not self._metadata.empty:
            meta_path = self._index_path + ".meta.parquet"
            self._metadata.to_parquet(meta_path, index=False)

    # ── BaseConnector required methods ──────────────────────────────

    async def list_tables(self) -> List[str]:
        """A FAISS connector exposes exactly one logical table."""
        return [self.DEFAULT_TABLE_NAME]

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Return the metadata frame's columns + the embedding dimension."""
        if not self._is_connected:
            return {}
        cols: List[Dict[str, str]] = [
            {"name": "id", "type": "integer"},
            {"name": "embedding", "type": f"vector({self._dim})"},
        ]
        if self._metadata is not None:
            for col in self._metadata.columns:
                if col == "id":
                    continue
                cols.append({"name": col, "type": str(self._metadata[col].dtype)})
        return {
            "table_name": table_name,
            "columns": cols,
            "n_vectors": int(self._index.ntotal) if self._index is not None else 0,
        }

    async def sample_rows(
        self,
        table_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` metadata rows (without the embedding
        vectors themselves — they're large and noisy in chat output)."""
        if self._metadata is None or self._metadata.empty:
            return []
        return self._metadata.head(limit).to_dict(orient="records")

    async def execute_query(
        self,
        query: str,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """FAISS doesn't have a SQL surface. ``execute_query`` is a
        no-op that returns the connector's metadata frame as a fallback
        — callers should use ``vector_search()`` for the actual
        similarity operation."""
        logger.info(
            "FAISSConnector.execute_query called with query=%r — "
            "FAISS has no SQL; returning metadata sample. "
            "Use vector_search() for similarity.", query[:80],
        )
        return await self.sample_rows(self.DEFAULT_TABLE_NAME, limit=limit)

    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Basic profile: vector count, dimension, index type."""
        if self._index is None:
            return {}
        return {
            "table_name": table_name,
            "n_vectors": int(self._index.ntotal),
            "dimension": self._dim,
            "index_class": type(self._index).__name__,
            "metadata_columns": (
                list(self._metadata.columns) if self._metadata is not None else []
            ),
        }

    # ── Sprint 17: multi-modal capabilities ─────────────────────────

    def capabilities(self) -> Dict[str, bool]:
        """FAISS supports vector ops only — no SQL, no spatial."""
        return {"sql": False, "vector": True, "spatial": False}

    async def vector_search(
        self,
        table: str,
        embedding: List[float],
        *,
        column: str = "embedding",
        limit: int = 10,
        metric: str = "cosine",
    ) -> List[Dict[str, Any]]:
        """Nearest-neighbour search. Returns the top-``limit`` metadata
        rows ranked by distance, with a ``_distance`` field added.

        The ``metric`` argument is informational here — the index was
        built with a fixed metric at ``connect()`` time. Cosine vs L2
        is determined by the index type, not the query. If the caller
        requests a metric the index doesn't support, we log a warning
        and search with whatever the index has.
        """
        if not self._is_connected or self._index is None:
            return []
        import numpy as np

        params = self.config.extra_params or {}
        configured_metric = params.get("index_type", "flat_ip")
        if metric == "cosine" and configured_metric != "flat_ip":
            logger.warning(
                "FAISS vector_search requested cosine but index is %s; "
                "results use the index's native metric.", configured_metric,
            )

        with self._lock:
            n_vectors = int(self._index.ntotal)
            if n_vectors == 0:
                return []
            q = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
            if q.shape[1] != self._dim:
                logger.warning(
                    "FAISS vector_search dimension mismatch: query=%d, index=%d",
                    q.shape[1], self._dim,
                )
                return []
            k = min(limit, n_vectors)
            distances, indices = self._index.search(q, k)

        results: List[Dict[str, Any]] = []
        for rank, (dist, idx) in enumerate(zip(distances[0], indices[0])):
            if idx < 0:   # FAISS returns -1 for "not enough vectors"
                continue
            row: Dict[str, Any] = {"_rank": rank, "_distance": float(dist)}
            if self._metadata is not None and idx < len(self._metadata):
                row.update(self._metadata.iloc[int(idx)].to_dict())
            else:
                row["id"] = int(idx)
            results.append(row)
        return results

    # ── FAISS-specific mutation surface ─────────────────────────────

    async def add_vectors(
        self,
        vectors: List[List[float]],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Append vectors + optional per-row metadata. Returns the
        starting integer ID assigned to the first new vector — useful
        when the caller needs stable IDs to join against."""
        if not self._is_connected or self._index is None:
            raise RuntimeError("FAISSConnector not connected")
        import numpy as np
        import pandas as pd

        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != self._dim:
            raise ValueError(
                f"vectors must be 2-D of shape (n, {self._dim}); got {arr.shape}"
            )
        with self._lock:
            start_id = int(self._index.ntotal)
            self._index.add(arr)
            new_meta_rows: List[Dict[str, Any]] = []
            for i, _ in enumerate(arr):
                row = {"id": start_id + i}
                if metadata and i < len(metadata):
                    row.update(metadata[i])
                new_meta_rows.append(row)
            new_frame = pd.DataFrame(new_meta_rows)
            if self._metadata is None or self._metadata.empty:
                self._metadata = new_frame
            else:
                self._metadata = pd.concat(
                    [self._metadata, new_frame], ignore_index=True,
                )
        # Persist immediately if a path is configured — auditors expect
        # write durability without a separate flush call.
        if self._index_path:
            try:
                await self._persist()
            except Exception as exc:
                logger.warning(
                    "FAISSConnector persist after add_vectors failed: %s", exc,
                )
        return start_id


# Re-export for symmetry with the other connector modules
__all__ = ["FAISSConnector"]
