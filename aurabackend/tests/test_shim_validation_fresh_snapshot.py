"""BUG-416: a shim's output is judged against its own schema, not the pre-shim one."""
from __future__ import annotations

import pytest

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DriftType, ShimResult, infer_schema_snapshot, set_transformed_rows
from uasr.recovery_loop import RecoveryLoop

_RENAME_BACK = (
    "def transform(rows: list[dict]) -> list[dict]:\n"
    "    return [{('amount' if k == 'amt' else k): v for k, v in row.items()} for row in rows]\n"
)
_NO_OP = "def transform(rows: list[dict]) -> list[dict]:\n    return rows\n"


def _batch(amount_key: str, batch_id: str) -> BatchPayload:
    rows = [{"id": i, amount_key: 10.5 + i, "note": "x"} for i in range(30)]
    cols = sorted(rows[0])
    return BatchPayload(source_id="s1", batch_id=batch_id, columns=cols, rows=rows,
                        schema_snapshot=infer_schema_snapshot(rows, cols))


def _drifted():
    detector = DriftDetector()
    detector.register_baseline("s1", _batch("amount", "b0"))
    batch = _batch("amt", "b1")
    drift = detector.detect(batch, record_state=False)
    assert drift.drift_type == DriftType.SCHEMA
    return detector, batch, drift


@pytest.mark.asyncio
async def test_a_shim_that_undoes_a_rename_passes_validation():
    detector, batch, drift = _drifted()
    result = await RecoveryLoop(detector)._validate_shim(
        ShimResult(recovery_id="r1", shim_code=_RENAME_BACK), batch, drift)
    assert result["passed"], result


@pytest.mark.asyncio
async def test_a_shim_that_leaves_the_schema_broken_says_so():
    detector, batch, drift = _drifted()
    result = await RecoveryLoop(detector)._validate_shim(
        ShimResult(recovery_id="r1", shim_code=_NO_OP), batch, drift)
    assert not result["passed"]
    assert "Schema drift still present" in result["reason"]


def test_a_deployed_rename_shim_stops_the_drift_refiring():
    detector, batch, _ = _drifted()
    set_transformed_rows(batch, [{("amount" if k == "amt" else k): v for k, v in r.items()} for r in batch.rows])
    assert not detector.detect(batch, record_state=False).drift_type == DriftType.SCHEMA
    assert "amount" in batch.schema_snapshot and "amt" not in batch.schema_snapshot
