"""BUG-267 / BUG-268: MAPE-K worker lifecycle.

267: after a failed recovery the loop parked on its STOP signal, so
POST /uasr/mapek/resume reported success but nothing was consumed until a restart.
268: an unexpected exception ended the loop task silently -- the worker still said
'running', consumed nothing, and stop() then re-raised and skipped its cleanup.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402


class _Consumer:
    def __init__(self) -> None:
        self.rewinds = 0
        self.stopped = False

    async def seek_to_committed(self) -> None:
        self.rewinds += 1

    async def stop(self) -> None:
        self.stopped = True


def _worker(tmp_path) -> MAPEKWorker:
    worker = MAPEKWorker(MAPEKConfig(
        source_id="src", duckdb_path=str(tmp_path / "lake.duckdb"), parquet_dir=str(tmp_path / "parquet")))
    worker._consumer = _Consumer()

    async def _no_emit(*args, **kwargs):
        return None

    worker._emit = _no_emit
    return worker


async def _until(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_paused_worker_consumes_again_after_resume_and_rereads_the_parked_batch(tmp_path):
    worker = _worker(tmp_path)
    pulls = 0

    async def _pull():
        nonlocal pulls
        pulls += 1
        await asyncio.sleep(0.01)
        return None  # no rows: the loop just goes round

    worker._monitor_pull_batch = _pull
    # The state a failed recovery leaves behind: paused, with an uncommitted batch.
    worker.pause(reason="recovery failed")
    worker._needs_rewind = True
    worker._running = True
    task = asyncio.create_task(worker._run_forever())
    await asyncio.sleep(0.1)
    assert pulls == 0  # parked

    worker.resume()

    await _until(lambda: pulls >= 2)
    assert worker._consumer.rewinds == 1  # went back to the last committed offsets, once
    worker._stop_signal.set()
    await asyncio.wait_for(task, timeout=3)


@pytest.mark.asyncio
async def test_stop_while_paused_returns_promptly(tmp_path):
    worker = _worker(tmp_path)
    worker.pause(reason="held")
    worker._running = True
    worker._task = asyncio.create_task(worker._run_forever())
    await asyncio.sleep(0.05)

    await asyncio.wait_for(worker.stop(), timeout=3)

    assert worker._consumer.stopped


@pytest.mark.asyncio
async def test_a_loop_error_pauses_the_worker_instead_of_killing_it_silently(tmp_path):
    worker = _worker(tmp_path)
    calls = 0

    async def _pull():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected")
        await asyncio.sleep(0.01)
        return None

    worker._monitor_pull_batch = _pull
    worker._running = True
    worker._task = asyncio.create_task(worker._run_forever())

    await _until(lambda: worker.is_paused)
    assert not worker._task.done(), "the loop task died instead of pausing"
    assert worker._last_error == "RuntimeError"

    worker.resume()
    await _until(lambda: calls >= 3)
    assert worker._last_error is None and worker._consumer.rewinds == 1

    await asyncio.wait_for(worker.stop(), timeout=3)
    assert worker._consumer.stopped


@pytest.mark.asyncio
async def test_stop_still_cleans_up_when_the_loop_task_failed(tmp_path):
    worker = _worker(tmp_path)

    async def _boom():
        raise RuntimeError("task ended with an exception")

    worker._running = True
    worker._task = asyncio.create_task(_boom())
    await asyncio.sleep(0.01)

    await worker.stop()

    assert worker._consumer.stopped


@pytest.mark.asyncio
async def test_resume_after_a_real_failed_recovery_consumes_again(tmp_path):
    """Drives the actual failed-recovery branch of the loop (the BUG-267 path): on the
    old code the loop parks on the stop signal there and never pulls again."""
    import types

    from uasr.models import BatchPayload, RecoveryStatus

    worker = _worker(tmp_path)
    worker._shim_router = None
    worker._numeric_analyzer = None
    pulls = 0

    async def _pull():
        nonlocal pulls
        pulls += 1
        await asyncio.sleep(0.01)
        if pulls == 1:
            return BatchPayload(source_id="src", batch_id="b1", columns=["v"], rows=[{"v": 1}])
        return None

    drift = types.SimpleNamespace(
        drift_detected=True, drift_type="schema", severity="high", batch_id="b1", drift_vector={}, details="")
    failed = types.SimpleNamespace(recovery_id="r1", status=RecoveryStatus.FAILED, shim=None)

    async def _failed_recovery(*args, **kwargs):
        return failed

    async def _noop(*args, **kwargs):
        return None

    worker._monitor_pull_batch = _pull
    worker._analyze_detect_drift = lambda batch: drift
    worker._should_pause = lambda d: True
    worker._plan_recovery = _failed_recovery
    worker._knowledge_update = _noop
    worker._persist_and_maybe_cross_heal = _noop
    worker._loop = types.SimpleNamespace(
        apply_shims=lambda source_id, rows: rows,
        get_deployed_shims=lambda source_id: [],
        check_post_deploy=lambda source_id, d: False,
    )
    worker._running = True
    worker._task = asyncio.create_task(worker._run_forever())

    await _until(lambda: worker.is_paused and pulls == 1)
    await asyncio.sleep(0.1)
    assert pulls == 1  # held for an operator

    worker.resume()

    await _until(lambda: pulls >= 3)  # consuming again
    assert worker._consumer.rewinds == 1  # and the uncommitted batch is re-read
    await asyncio.wait_for(worker.stop(), timeout=3)
