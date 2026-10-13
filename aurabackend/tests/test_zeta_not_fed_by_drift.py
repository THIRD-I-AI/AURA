"""BUG-417: a batch flagged as drifted must not raise the adaptive threshold."""
from __future__ import annotations

import random
import re

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload


def _batch(seed: int, scale: float = 1.0) -> BatchPayload:
    rng = random.Random(seed)
    rows = [{"amount": rng.gauss(100, 10) * scale, "qty": rng.gauss(5, 1)} for _ in range(500)]
    return BatchPayload(source_id="s1", batch_id=f"b{seed}", columns=["amount", "qty"], rows=rows)


def _zeta(result) -> float:
    return float(re.search(r"=([0-9.]+) in", result.details).group(1))


def test_redetecting_a_unit_bug_leaves_the_threshold_where_it_was():
    detector = DriftDetector()
    detector.register_baseline("s1", _batch(0))
    for seed in range(1, 31):
        detector.detect(_batch(seed))

    # A failed recovery followed by an operator resume re-detects the same batch.
    thresholds = []
    for _ in range(4):
        result = detector.detect(_batch(50, scale=100))
        assert result.drift_detected
        thresholds.append(_zeta(result))
    assert thresholds == [thresholds[0]] * 4
