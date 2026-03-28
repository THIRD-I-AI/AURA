"""
UASR Semantic Gateway — Batch Embedding & Reference Context Matrix
====================================================================
Manages the semantic alignment layer:

  - Computes batch-level embeddings  Eᵦ  via hash-projection or LLM
  - Maintains a Reference Context Matrix  M_ref  per data source
  - Provides a semantic "gate" that rejects batches drifting beyond
    a configurable cosine-distance threshold
  - Supports versioned baselines for A/B comparison and rollback

Equation reference (UASR paper §4):
    sim(Eᵦ, M_ref) = cos(Eᵦ, M_ref)
    Gate opens when sim ≥ (1 − τ)
"""
from __future__ import annotations

import hashlib
import logging
import math
import statistics
import struct
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import BatchPayload, ColumnDistribution

logger = logging.getLogger("uasr.semantic_gateway")


# ────────────────────────────────────────────────────────────────────
# Embedding helpers
# ────────────────────────────────────────────────────────────────────

def hash_project(data: bytes, dim: int = 256) -> List[float]:
    """
    Deterministic hash-projection embedding.

    Creates a *dim*-dimensional vector by hashing overlapping windows
    of the serialised data and mapping each hash to [-1, 1].
    The result is L2-normalised.
    """
    vec = [0.0] * dim
    for i in range(dim):
        h = hashlib.sha256(data + struct.pack("<I", i)).digest()
        # Use 4-byte unsigned int mapped to [-1, 1] (safe — no NaN/Inf)
        raw = struct.unpack("<I", h[:4])[0]
        vec[i] = (raw / 0xFFFFFFFF) * 2.0 - 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def batch_embedding(batch: BatchPayload, dim: int = 256) -> List[float]:
    """Compute a fixed-size embedding for a whole batch."""
    serialised = repr(sorted(batch.columns)).encode() + b"|"
    for row in batch.rows[:200]:           # cap to avoid huge hashes
        serialised += repr(sorted(row.items())).encode()
    return hash_project(serialised, dim)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity of two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ────────────────────────────────────────────────────────────────────
# Reference Context Matrix
# ────────────────────────────────────────────────────────────────────

class ReferenceVersion:
    """One version of a reference embedding for a data source."""

    __slots__ = ("version_id", "embedding", "created_at", "description", "active")

    def __init__(
        self,
        embedding: List[float],
        description: str = "",
    ) -> None:
        self.version_id: str = uuid.uuid4().hex[:12]
        self.embedding = embedding
        self.created_at = datetime.now(timezone.utc)
        self.description = description
        self.active = True


class ReferenceContextMatrix:
    """
    Manages versioned reference embeddings per data source.

    Each source can have multiple versions; only one is *active*.
    Similarity is always computed against the active version.
    """

    def __init__(self) -> None:
        # source_id  →  [ReferenceVersion, …]
        self._store: Dict[str, List[ReferenceVersion]] = defaultdict(list)

    # ── Write ───────────────────────────────────────────────────────

    def register(
        self,
        source_id: str,
        embedding: List[float],
        description: str = "",
    ) -> str:
        """Register a new reference version and activate it."""
        # Deactivate previous
        for v in self._store[source_id]:
            v.active = False

        version = ReferenceVersion(embedding, description)
        self._store[source_id].append(version)
        logger.info(
            "Registered reference v=%s for source=%s (%d dims)",
            version.version_id,
            source_id,
            len(embedding),
        )
        return version.version_id

    def register_from_batch(
        self,
        batch: BatchPayload,
        dim: int = 256,
        description: str = "auto",
    ) -> str:
        """Convenience: embed a batch and register it as the new reference."""
        emb = batch_embedding(batch, dim)
        return self.register(batch.source_id, emb, description)

    def activate_version(self, source_id: str, version_id: str) -> bool:
        """Activate a specific version (for rollback / A-B switching)."""
        found = False
        for v in self._store[source_id]:
            if v.version_id == version_id:
                v.active = True
                found = True
            else:
                v.active = False
        return found

    # ── Read ────────────────────────────────────────────────────────

    def active_embedding(self, source_id: str) -> Optional[List[float]]:
        """Return the active reference embedding, or None."""
        for v in reversed(self._store[source_id]):
            if v.active:
                return v.embedding
        return None

    def list_versions(self, source_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "version_id": v.version_id,
                "created_at": v.created_at.isoformat(),
                "active": v.active,
                "description": v.description,
            }
            for v in self._store[source_id]
        ]


# ────────────────────────────────────────────────────────────────────
# Semantic Gateway
# ────────────────────────────────────────────────────────────────────

class GateDecision:
    """Result of a gateway check."""

    __slots__ = ("allowed", "similarity", "threshold", "source_id", "batch_id", "reason")

    def __init__(
        self,
        allowed: bool,
        similarity: float,
        threshold: float,
        source_id: str,
        batch_id: str,
        reason: str = "",
    ) -> None:
        self.allowed = allowed
        self.similarity = similarity
        self.threshold = threshold
        self.source_id = source_id
        self.batch_id = batch_id
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "similarity": round(self.similarity, 6),
            "threshold": round(self.threshold, 6),
            "source_id": self.source_id,
            "batch_id": self.batch_id,
            "reason": self.reason,
        }


class SemanticGateway:
    """
    Acts as the semantic "gate" for incoming batches.

    A batch passes the gate iff:
        cos(Eᵦ, M_ref) ≥ 1 − τ

    where τ is the tolerance (default 0.25 → similarity ≥ 0.75).
    """

    def __init__(
        self,
        matrix: Optional[ReferenceContextMatrix] = None,
        tolerance: float = 0.25,
        embedding_dim: int = 256,
    ) -> None:
        self._matrix = matrix or ReferenceContextMatrix()
        self._tolerance = tolerance
        self._dim = embedding_dim
        # history for adaptive threshold
        self._sim_history: Dict[str, List[float]] = defaultdict(list)

    @property
    def matrix(self) -> ReferenceContextMatrix:
        return self._matrix

    # ── Gate check ──────────────────────────────────────────────────

    def check(self, batch: BatchPayload) -> GateDecision:
        """Run the semantic gate on a batch."""
        ref_emb = self._matrix.active_embedding(batch.source_id)

        if ref_emb is None:
            # No reference registered → auto-register this batch as baseline
            self._matrix.register_from_batch(batch, self._dim, "auto-baseline")
            return GateDecision(
                allowed=True,
                similarity=1.0,
                threshold=1 - self._tolerance,
                source_id=batch.source_id,
                batch_id=batch.batch_id,
                reason="First batch — registered as baseline",
            )

        batch_emb = batch_embedding(batch, self._dim)
        sim = cosine_similarity(batch_emb, ref_emb)

        # Record for adaptive threshold
        self._sim_history[batch.source_id].append(sim)

        threshold = self._adaptive_threshold(batch.source_id)
        passed = sim >= threshold

        decision = GateDecision(
            allowed=passed,
            similarity=sim,
            threshold=threshold,
            source_id=batch.source_id,
            batch_id=batch.batch_id,
            reason="" if passed else f"Similarity {sim:.4f} < threshold {threshold:.4f}",
        )

        if not passed:
            logger.warning(
                "Semantic gate REJECTED batch %s (source=%s): sim=%.4f < threshold=%.4f",
                batch.batch_id,
                batch.source_id,
                sim,
                threshold,
            )

        return decision

    # ── Adaptive threshold ──────────────────────────────────────────

    def _adaptive_threshold(self, source_id: str) -> float:
        """
        Compute an adaptive threshold based on historical similarities:
            threshold = max(1 − τ, mean(sim) − 2·std(sim))
        This prevents the gate from being too strict or too lax.
        """
        history = self._sim_history.get(source_id, [])
        base = 1 - self._tolerance

        if len(history) < 5:
            return base

        mu = statistics.mean(history[-50:])   # rolling window
        sigma = statistics.stdev(history[-50:])
        adaptive = mu - 2 * sigma

        return max(base * 0.8, min(adaptive, base))

    # ── Passthrough with auto-registration ──────────────────────────

    def process(
        self,
        batch: BatchPayload,
        on_reject: Optional[str] = "flag",
    ) -> Tuple[bool, GateDecision]:
        """
        Process a batch through the gate.

        Args:
            batch: Incoming batch to evaluate.
            on_reject: What to do when gate rejects:
                       "flag"   — allow through but flag (default)
                       "block"  — reject entirely
                       "auto"   — trigger drift detection downstream

        Returns:
            (allowed, decision)
        """
        decision = self.check(batch)

        if decision.allowed:
            return True, decision

        if on_reject == "block":
            return False, decision

        # "flag" or "auto" — allow through but mark
        decision.reason += " [flagged]"
        return True, decision

    # ── Reference management shortcuts ──────────────────────────────

    def register_baseline(self, batch: BatchPayload, desc: str = "manual") -> str:
        return self._matrix.register_from_batch(batch, self._dim, desc)

    def rollback_reference(self, source_id: str, version_id: str) -> bool:
        return self._matrix.activate_version(source_id, version_id)

    def reference_versions(self, source_id: str) -> List[Dict[str, Any]]:
        return self._matrix.list_versions(source_id)
