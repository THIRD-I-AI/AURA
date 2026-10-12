"""BUG-415: a batch's schema comes from all of its rows, not the first one."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from uasr.drift_detector import DriftDetector
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
from uasr.models import DriftType


async def _pull(rows):
    class _Consumer:
        async def getmany(self, timeout_ms, max_records):
            return {"tp": [SimpleNamespace(value=r) for r in rows]}

    worker = MAPEKWorker(config=MAPEKConfig(source_id="s", batch_size=len(rows), batch_window_seconds=5))
    worker._consumer = _Consumer()
    return await worker._monitor_pull_batch()


def _rows(first):
    return [first] + [{"id": i, "amount": 10.5 + i, "note": "x"} for i in range(2, 30)]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [
    {"id": 1, "amount": 10.5, "note": None},  # null in the first row
    {"id": 1, "amount": 10, "note": "x"},  # JSON whole number in a float column
    {"id": 1, "amount": 10.5},  # key absent from the first row
])
async def test_an_unrepresentative_first_row_is_not_schema_drift(first):
    detector = DriftDetector()
    assert not detector.detect(await _pull(_rows({"id": 1, "amount": 10.5, "note": "x"}))).drift_detected
    result = detector.detect(await _pull(_rows(first)))
    assert result.drift_type != DriftType.SCHEMA, result


@pytest.mark.asyncio
async def test_a_real_retype_and_a_real_removal_are_still_schema_drift():
    detector = DriftDetector()
    detector.detect(await _pull(_rows({"id": 1, "amount": 10.5, "note": "x"})))
    retyped = [{"id": i, "amount": "ten", "note": "x"} for i in range(30)]
    assert detector.detect(await _pull(retyped)).drift_type == DriftType.SCHEMA

    detector = DriftDetector()
    detector.detect(await _pull(_rows({"id": 1, "amount": 10.5, "note": "x"})))
    removed = [{"id": i, "amount": 10.5} for i in range(30)]
    assert detector.detect(await _pull(removed)).drift_type == DriftType.SCHEMA


@pytest.mark.asyncio
async def test_a_column_first_seen_as_all_null_gets_its_type_later():
    detector = DriftDetector()
    detector.detect(await _pull([{"id": i, "note": None} for i in range(30)]))
    detector.detect(await _pull([{"id": i, "note": "x"} for i in range(30)]))
    result = detector.detect(await _pull([{"id": i, "note": 5} for i in range(30)]))
    assert result.drift_type == DriftType.SCHEMA
