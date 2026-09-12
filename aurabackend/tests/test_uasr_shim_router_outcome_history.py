"""
DSR-007a — ShimRouter canary outcome-history plumbing.

Before this fix, ShimRouter tracked routing weights and canary_scores
but had no record of what actually happened to a routed batch (which
version handled it, what the outcome was) -- there was no history for a
future causal estimate (DSR-007b/c) to learn from. This is plumbing
only: no causal math, no change to shim-selection behavior.

Tier A (pure unit, no Kafka): ShimRouter.record_outcome/outcome_history
directly. Tier B (integration): a real MAPEKWorker with a fake Kafka
consumer, proving _run_forever actually calls record_outcome for a real
routed batch -- matches test_uasr_mapek_kafka_bootstrap_retry.py's
pattern of driving the real loop end-to-end.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
import uasr.mapek_worker as mapek_worker  # noqa: E402
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402
from uasr.shim_router import OutcomeRecord, ShimRouter  # noqa: E402

# ── Tier A: ShimRouter.record_outcome / outcome_history ────────────────


def test_record_outcome_appends_a_record():
    router = ShimRouter()
    router.record_outcome("src", "v1", 1.0, 1000.0, {"row_count": 50})

    history = router.outcome_history("src")
    assert history == [OutcomeRecord(version="v1", outcome=1.0, timestamp=1000.0, covariates={"row_count": 50})]


def test_outcome_history_empty_for_unknown_source():
    router = ShimRouter()
    assert router.outcome_history("nobody") == []


def test_outcome_history_bounded_evicts_oldest():
    router = ShimRouter(outcome_history_capacity=3)
    for i in range(5):
        router.record_outcome("src", "v1", float(i), float(i), {})

    history = router.outcome_history("src")
    assert len(history) == 3, f"expected capacity to cap at 3, got {len(history)}"
    # Oldest two (outcome=0.0, 1.0) must be evicted; the 3 most recent survive.
    assert [r.outcome for r in history] == [2.0, 3.0, 4.0]


def test_outcome_history_returns_a_copy_not_live_state():
    router = ShimRouter()
    router.record_outcome("src", "v1", 1.0, 1.0, {})
    snapshot = router.outcome_history("src")
    snapshot.append(OutcomeRecord(version="tampered", outcome=0.0, timestamp=0.0))
    assert len(router.outcome_history("src")) == 1, (
        "outcome_history() must return a defensive copy, not the live list"
    )


# ── Tier B: real _run_forever loop records an outcome ───────────────────


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    """Own temp-file SQLite DB -- same reasoning/teardown as
    test_uasr_mapek_kafka_bootstrap_retry.py (BUG-008: an orphaned
    aiosqlite connection pool's non-daemon threads hang pytest at exit)."""
    original_url = _metadata_db.DATABASE_URL
    original_engine = _metadata_db._engine
    original_factory = _metadata_db._session_factory

    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_test_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None

    yield

    leaked_engine = _metadata_db._engine
    if leaked_engine is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(leaked_engine.dispose())
        finally:
            loop.close()

    _metadata_db.DATABASE_URL = original_url
    _metadata_db._engine = original_engine
    _metadata_db._session_factory = original_factory
    try:
        os.remove(tmp_path)
    except OSError:
        pass


class _OneShotConsumer:
    """Serves exactly one real batch, then empty forever -- no Kafka
    outage simulation needed here (that's test_uasr_mapek_kafka_bootstrap_retry.py's
    concern), just a real batch flowing through the real loop once."""

    def __init__(self, *args, **kwargs):
        self._served = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def getmany(self, timeout_ms: int = 0, max_records: int = 0):
        await asyncio.sleep(0.01)  # real suspension point -- see reference test
        if self._served:
            return {}
        self._served = True

        class _Msg:
            def __init__(self, value):
                self.value = value

        rows = [{"v": float(i)} for i in range(20)]
        return {"tp-0": [_Msg(r) for r in rows]}


async def _passthrough(source_id: str, rows):
    return rows


@pytest.mark.asyncio
async def test_run_forever_records_outcome_for_a_routed_batch(monkeypatch):
    tmp_dir = tempfile.mkdtemp(prefix="aura_uasr_dsr007a_test_")
    duckdb_path = os.path.join(tmp_dir, "lake.duckdb")
    parquet_dir = os.path.join(tmp_dir, "parquet")

    cfg = MAPEKConfig(
        source_id="src",
        duckdb_path=duckdb_path,
        parquet_dir=parquet_dir,
        batch_size=20,
        batch_window_seconds=2.0,
        use_shim_router=True,
    )
    worker = MAPEKWorker(config=cfg)
    assert worker._shim_router is not None
    await worker._shim_router.add_route("src", "v1", _passthrough)

    monkeypatch.setattr(mapek_worker, "AIOKafkaConsumer", _OneShotConsumer)
    monkeypatch.setattr(mapek_worker, "_AIOKAFKA_AVAILABLE", True)

    await worker.start()
    try:
        history = []
        for _ in range(200):
            history = worker._shim_router.outcome_history("src")
            if history:
                break
            await asyncio.sleep(0.02)

        assert history, (
            "no outcome was recorded for the routed batch -- "
            "_run_forever's record_outcome wiring did not fire"
        )
        record = history[0]
        assert record.version == "v1"
        assert record.covariates.get("row_count") == 20
    finally:
        await worker.stop()
