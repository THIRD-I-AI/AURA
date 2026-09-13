"""
DSR-009 ultracode-review finding — ConformalMartingaleRegistry.register_baseline
must REPLACE the per-source baseline/detector dict on each call, matching
WassersteinMartingaleDetector.register_baseline's semantics. It was merging
instead, so a column dropped from a re-baseline call (e.g. all-null in that
batch) kept its stale detector alive indefinitely instead of being cleared.
"""
from __future__ import annotations

from uasr.conformal_martingale import ConformalMartingaleRegistry


def test_register_baseline_drops_columns_missing_from_new_call():
    registry = ConformalMartingaleRegistry(warmup=2)
    registry.register_baseline("src", {"a": [1.0] * 20, "b": [2.0] * 20})
    assert "a" in registry._detectors["src"]
    assert "b" in registry._detectors["src"]

    # Re-baseline with column "b" absent (e.g. all-null in this batch).
    registry.register_baseline("src", {"a": [1.5] * 20})

    assert "a" in registry._detectors["src"]
    assert "b" not in registry._detectors["src"], (
        "stale detector for a column dropped from re-baselining must be "
        "cleared, not kept alive indefinitely"
    )
    assert "b" not in registry._baselines["src"]
    assert registry.update("src", "b", [2.0] * 5) is False, (
        "update() must report no-baseline for the dropped column"
    )
