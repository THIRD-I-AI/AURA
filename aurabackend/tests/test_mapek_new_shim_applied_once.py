"""BUG-344: after a recovery deployed a new shim, the MAPE-K pause path re-ran the whole
shim chain on rows the standing shims had already transformed, so every standing shim
ran twice -- a rescale shim divided by its factor twice, and that corrupted batch was
persisted and re-registered as the detector baseline."""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr import mapek_worker as mw  # noqa: E402
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402
from uasr.models import BatchPayload, RecoveryStatus  # noqa: E402

RESCALE = "def transform(rows):\n    return [{**r, 'v': r['v'] * 10} for r in rows]\n"
PLUS_ONE = "def transform(rows):\n    return [{**r, 'v': r['v'] + 1} for r in rows]\n"


@pytest.mark.asyncio
async def test_the_pause_path_applies_only_the_newly_deployed_shim(tmp_path, monkeypatch):
    worker = MAPEKWorker(MAPEKConfig(
        source_id="src", duckdb_path=str(tmp_path / "lake.duckdb"), parquet_dir=str(tmp_path / "parquet")))
    worker._loop.hydrate_deployed_shims({"src": [RESCALE]})
    pulled = False
    persisted: list = []

    async def _noop(*args, **kwargs):
        return None

    async def _pull():
        nonlocal pulled
        if pulled:
            await asyncio.sleep(0.01)
            return None
        pulled = True
        return BatchPayload(source_id="src", batch_id="b1", columns=["v"], rows=[{"v": 1}])

    async def _plan(drift, batch):
        worker._loop._deployed_shims["src"].append(PLUS_ONE)
        return types.SimpleNamespace(
            status=RecoveryStatus.DEPLOYED, recovery_id="r1", shim=types.SimpleNamespace(shim_code=PLUS_ONE))

    async def _persist(batch):
        persisted.append([dict(r) for r in batch.rows])

    drift = types.SimpleNamespace(drift_detected=True, drift_type="schema", severity="high")
    worker._emit = _noop
    worker._monitor_pull_batch = _pull
    worker._analyze_detect_drift = lambda batch: drift
    worker._should_pause = lambda d: True
    worker._plan_recovery = _plan
    worker._execute_persist = _persist
    worker._knowledge_update = _noop
    worker._loop.check_post_deploy = lambda *a, **k: False
    monkeypatch.setattr(mw, "persist_recovery_row", _noop)

    worker._running = True
    task = asyncio.create_task(worker._run_forever())
    deadline = asyncio.get_running_loop().time() + 3
    while not persisted and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    worker._running = False
    worker._stop_signal.set()
    await asyncio.wait_for(task, timeout=3)

    assert persisted == [[{"v": 11}]], "the standing rescale shim ran a second time"
