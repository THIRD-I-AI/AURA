"""BUG-418: a numeric column that starts carrying text is drift, not a zero score."""
from __future__ import annotations

import random

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DriftType


def _batch(seed: int, amount) -> BatchPayload:
    rng = random.Random(seed)
    rows = [{"amount": amount(rng, i), "qty": rng.gauss(5, 1)} for i in range(500)]
    return BatchPayload(source_id="s1", batch_id=f"b{seed}", columns=["amount", "qty"], rows=rows)


def _detector() -> DriftDetector:
    detector = DriftDetector()
    detector.register_baseline("s1", _batch(0, lambda rng, i: rng.gauss(100, 10)))
    return detector


def test_text_in_a_numeric_column_is_reported_as_drift():
    corrupted = _batch(1, lambda rng, i: "N/A" if i % 2 else rng.gauss(100, 10) * 1000)
    result = _detector().detect(corrupted)
    assert result.drift_detected and result.drift_type == DriftType.STATISTICAL
    assert "amount" in result.affected_columns


def test_the_flip_does_not_move_the_adaptive_threshold():
    detector = _detector()
    corrupted = _batch(1, lambda rng, i: "N/A" if i % 2 else rng.gauss(100, 10))
    for _ in range(3):
        detector.detect(corrupted)
    assert detector._kl_history.get("s1", []) == []
