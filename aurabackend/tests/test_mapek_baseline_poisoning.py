"""Regression tests for fix 3b — post-recovery baseline poisoning.

``MAPEKWorker._knowledge_update`` must only advance the statistical baseline
when handed a *confirmed post-shim* batch (``batch_healed=True``), and it must
do so via the distribution-dict path so the schema and semantic
reference-embedding baselines are preserved rather than clobbered.

Two hazards guarded here:
  1. Canary-deploy path persists the RAW DRIFTED batch → must NOT re-baseline.
  2. BatchPayload re-baseline path clobbers schema/embedding baselines →
     must snapshot only the statistical distributions.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.drift_detector import DriftDetector
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
)


def _batch(source_id, batch_id, loc, n=200, scale=2.0, seed=0):
    import random
    rnd = random.Random(f"{source_id}:{batch_id}:{seed}")
    return BatchPayload(
        source_id=source_id,
        batch_id=batch_id,
        rows=[{"value": rnd.gauss(loc, scale)} for _ in range(n)],
    )


def _deployed_recovery():
    return RecoveryLoopResult(
        drift_event_id="d0",
        recovery_id="r0",
        status=RecoveryStatus.DEPLOYED,
        total_latency_seconds=1.0,
    )


def _drift_result():
    return DriftDetectionResult(
        source_id="test_src",
        batch_id="b1",
        drift_detected=True,
        drift_type=DriftType.STATISTICAL,
        severity=DriftSeverity.HIGH,
    )


def _mean_of_baseline(det: DriftDetector, source_id: str, col: str = "value"):
    dists = det._baselines.get(source_id, {})
    cd = dists.get(col)
    return cd.mean if cd is not None else None


def test_canary_path_does_not_poison_baseline():
    """batch_healed=False (canary deploy) → statistical baseline unchanged."""
    det = DriftDetector()
    worker = MAPEKWorker(config=MAPEKConfig(source_id="test_src"), detector=det)

    clean = _batch("test_src", "base", loc=10.0)
    det.register_baseline("test_src", clean)
    base_mean_before = _mean_of_baseline(det, "test_src")

    # Canary deploy hands the RAW DRIFTED batch with batch_healed=False
    drifted = _batch("test_src", "drift", loc=100.0)
    asyncio.run(
        worker._knowledge_update(drifted, _drift_result(), _deployed_recovery(),
                                 batch_healed=False)
    )
    base_mean_after = _mean_of_baseline(det, "test_src")
    assert base_mean_after == base_mean_before  # NOT poisoned by drifted data


def test_healed_path_advances_baseline():
    """batch_healed=True (post-shim) → statistical baseline advances."""
    det = DriftDetector()
    worker = MAPEKWorker(config=MAPEKConfig(source_id="test_src"), detector=det)

    clean = _batch("test_src", "base", loc=10.0)
    det.register_baseline("test_src", clean)
    base_mean_before = _mean_of_baseline(det, "test_src")

    healed = _batch("test_src", "healed", loc=12.0)  # post-shim, slightly shifted
    asyncio.run(
        worker._knowledge_update(healed, _drift_result(), _deployed_recovery(),
                                 batch_healed=True)
    )
    base_mean_after = _mean_of_baseline(det, "test_src")
    assert base_mean_after != base_mean_before
    assert abs(base_mean_after - 12.0) < 1.0  # advanced toward the healed batch


def test_rebaseline_preserves_schema_and_embedding():
    """Dict-path re-baseline must not clobber schema or reference embedding."""
    det = DriftDetector()
    worker = MAPEKWorker(config=MAPEKConfig(source_id="test_src"), detector=det)

    # Register with an explicit schema + categorical col so an embedding exists
    import random
    rnd = random.Random("mix")
    rows = [{"value": rnd.gauss(10, 2), "cat": rnd.choice(["A", "B"])}
            for _ in range(200)]
    clean = BatchPayload(source_id="test_src", batch_id="base", rows=rows,
                         schema_snapshot={"value": "float", "cat": "str"})
    det.register_baseline("test_src", clean)
    schema_before = dict(det._schema_baselines.get("test_src", {}))
    emb_before = list(det._reference_embeddings.get("test_src", []))
    assert schema_before and emb_before  # both populated

    healed = BatchPayload(source_id="test_src", batch_id="healed",
                          rows=[{"value": rnd.gauss(12, 2), "cat": rnd.choice(["A", "B"])}
                                for _ in range(200)])
    asyncio.run(
        worker._knowledge_update(healed, _drift_result(), _deployed_recovery(),
                                 batch_healed=True)
    )
    # Schema + embedding baselines preserved (not overwritten by dict path)
    assert det._schema_baselines.get("test_src") == schema_before
    assert det._reference_embeddings.get("test_src") == emb_before


def test_no_recovery_no_rebaseline():
    """Happy-path batch (recovery=None) never touches the baseline."""
    det = DriftDetector()
    worker = MAPEKWorker(config=MAPEKConfig(source_id="test_src"), detector=det)
    clean = _batch("test_src", "base", loc=10.0)
    det.register_baseline("test_src", clean)
    before = _mean_of_baseline(det, "test_src")
    happy = _batch("test_src", "happy", loc=10.0)
    asyncio.run(worker._knowledge_update(happy, _drift_result(), None))
    assert _mean_of_baseline(det, "test_src") == before
