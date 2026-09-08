"""
Regression test for the confirmed live bug: the MAPE-K background worker
never started on the production box because aiokafka failed to bootstrap
to localhost:9092 (no Kafka broker in that deployment), the exception was
caught once, and ``_mapek_worker`` was permanently left ``None`` for the
container's entire lifetime -- so Monitor/Analyze/Plan/Execute in
mapek_worker.py never ran again, even after a broker would have become
reachable (aurabackend/uasr/service.py, was lines 283-308).

Live evidence (docker logs aura-uasr_service-1, 2026-09-08):
    UASR_MAPEK_ENABLED=true
    aiokafka | Unable connect to "localhost:9092": ... Connection refused
    uasr.service | MAPE-K worker failed to start: KafkaConnectionError: ...

The fix replaces the single try/except with ``service._mapek_worker_bootstrap``,
a background task that retries the Kafka connection with exponential backoff
(backend.md's "graceful connection recovery" rule, already applied to every
other network client in this codebase) instead of giving up forever.

This test proves both halves end-to-end, matching
test_mapek_martingale_integration.py's shape (construct a real MAPEKWorker,
drive it through drift -> heal with no mocks on the detector/recovery-loop
side, only on the Kafka transport):

  1. The bootstrap survives the exact failure mode reproduced above (a fake
     consumer whose ``start()`` raises the live box's ConnectionError twice)
     and still lands a running worker on the next attempt -- proving
     ``_mapek_worker`` is no longer stuck at None forever.
  2. Once connected, a real drifted batch flowing through that worker's own
     ``_run_forever`` loop reaches a real, deployed, non-vacuous shim and a
     persisted RecoveryRecord -- proving Monitor really does feed
     Analyze/Plan/Execute once the worker is up, not just that ``start()``
     returns.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
import uasr.mapek_worker as mapek_worker  # noqa: E402
import uasr.service as service  # noqa: E402
from uasr.db import get_session, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.mapek_worker import MAPEKConfig  # noqa: E402
from uasr.models import BatchPayload, DriftSeverity, RecoveryRecord, RecoveryStatus  # noqa: E402
from uasr.recovery_loop import RecoveryLoop  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    """Own temp-file SQLite DB, same reasoning/teardown as
    test_uasr_service_cross_source_heal.py (BUG-008: an orphaned aiosqlite
    connection pool's non-daemon threads hang pytest at interpreter exit)."""
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


@pytest.fixture(autouse=True)
async def _ensure_tables():
    await init_uasr_db()
    yield


class _FlakyThenHealthyConsumer:
    """Reproduces the live box's exact failure: the first attempts raise
    the same ConnectionError aiokafka wraps into KafkaConnectionError when
    no broker answers on localhost:9092; a later attempt succeeds and then
    serves exactly one real drifted batch."""

    attempts = 0
    fail_until_attempt = 3  # first 2 start() calls fail, 3rd succeeds

    def __init__(self, *args, **kwargs):
        self._served_batch = False

    async def start(self) -> None:
        type(self).attempts += 1
        if type(self).attempts < type(self).fail_until_attempt:
            raise ConnectionError(
                'Unable connect to "localhost:9092": Connection refused'
            )

    async def stop(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def getmany(self, timeout_ms: int = 0, max_records: int = 0):
        # A real await point matters here, not just realism: the worker's
        # own _monitor_pull_batch busy-polls this in a tight `while` up to
        # its deadline, and a getmany() with no genuine suspension point
        # never yields the event loop back to the test's own polling
        # coroutine -- starving it for the full batch_window_seconds on
        # every empty poll, indefinitely.
        await asyncio.sleep(0.01)
        if self._served_batch:
            return {}
        self._served_batch = True

        class _Msg:
            def __init__(self, value):
                self.value = value

        rnd = random.Random(7)
        rows = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(295)]
        rows += [{"v": 1500.0} for _ in range(5)]  # genuine outlier contamination
        return {"tp-0": [_Msg(r) for r in rows]}


@pytest.mark.asyncio
async def test_mapek_worker_recovers_from_kafka_outage_and_heals_real_drift(monkeypatch):
    _FlakyThenHealthyConsumer.attempts = 0

    tmp_dir = tempfile.mkdtemp(prefix="aura_uasr_mapek_test_")
    duckdb_path = os.path.join(tmp_dir, "lake.duckdb")
    parquet_dir = os.path.join(tmp_dir, "parquet")

    det = DriftDetector(default_zeta=0.05)
    rnd = random.Random(1)
    baseline_rows = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(300)]
    det.register_baseline("src", BatchPayload(source_id="src", columns=["v"], rows=baseline_rows))
    loop = RecoveryLoop(detector=det)

    cfg = MAPEKConfig(
        source_id="src",
        duckdb_path=duckdb_path,
        parquet_dir=parquet_dir,
        batch_size=300,
        batch_window_seconds=2.0,
        # LOW guarantees the recovery branch runs regardless of exactly which
        # severity tier this particular batch's KL divergence lands in --
        # what this test is proving is Monitor -> ... -> Execute wiring, not
        # the severity classifier's thresholds (covered elsewhere).
        pause_on_severity=DriftSeverity.LOW,
    )

    monkeypatch.setattr(mapek_worker, "AIOKafkaConsumer", _FlakyThenHealthyConsumer)
    monkeypatch.setattr(mapek_worker, "_AIOKAFKA_AVAILABLE", True)
    monkeypatch.setattr(service, "_mapek_config", lambda: cfg)
    monkeypatch.setattr(service, "_detector", det)
    monkeypatch.setattr(service, "_loop", loop)
    monkeypatch.setattr(service, "_repair_scheduler", None)
    monkeypatch.setattr(service, "_MAPEK_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(service, "_MAPEK_RETRY_MAX_SECONDS", 0.02)

    bootstrap_task = asyncio.create_task(service._mapek_worker_bootstrap())
    try:
        # 1) Prove the outage is survived: the fix means _mapek_worker
        #    eventually gets set instead of staying None after the first
        #    failure (the pre-fix behaviour this test guards against).
        for _ in range(200):
            if service._mapek_worker is not None:
                break
            await asyncio.sleep(0.01)
        assert service._mapek_worker is not None, (
            "MAPE-K worker never recovered from the simulated Kafka outage -- "
            "regressed to the old permanent-None behaviour"
        )
        assert _FlakyThenHealthyConsumer.attempts == 3, (
            f"expected exactly 2 failed attempts then 1 success, got "
            f"{_FlakyThenHealthyConsumer.attempts} attempts -- retry/backoff "
            f"wiring changed"
        )

        # 2) Prove Monitor -> Analyze -> Plan -> Execute actually ran end to
        #    end once connected: a real RecoveryRecord for source "src",
        #    DEPLOYED with a real (non-vacuous) clip shim.
        record: RecoveryRecord | None = None
        for _ in range(400):
            async for session in get_session():
                result = await session.execute(
                    select(RecoveryRecord).where(RecoveryRecord.source_id == "src")
                )
                record = result.scalars().first()
                break
            if record is not None:
                break
            await asyncio.sleep(0.02)

        assert record is not None, (
            "no RecoveryRecord was persisted -- drift reached Monitor/Analyze "
            "but Plan/Execute never ran (the exact symptom this bug reproduced)"
        )
        assert record.status == RecoveryStatus.DEPLOYED.value, (
            f"expected the outlier-contamination batch to auto-deploy a clip "
            f"shim, got status={record.status!r}"
        )
        assert record.shim_code is not None
        assert "_CLIP_BOUNDS" in record.shim_code, (
            "shim was not the real deterministic clip transform -- looks vacuous"
        )
    finally:
        if not bootstrap_task.done():
            bootstrap_task.cancel()
            try:
                await bootstrap_task
            except (asyncio.CancelledError, Exception):
                pass
        if service._mapek_worker is not None:
            try:
                await service._mapek_worker.stop()
            except Exception:
                pass
        service._mapek_worker = None
        service._mapek_bootstrap_task = None


@pytest.mark.asyncio
async def test_lifespan_starts_cleanly_with_mapek_enabled(monkeypatch):
    """Regression test for a real bug this fix's own first draft introduced:
    _lifespan() crashed with UnboundLocalError on every startup with
    UASR_MAPEK_ENABLED=true, because a redundant `import asyncio` later in
    the same function shadowed the module-level import for the entire
    function body (Python resolves scope statically) -- so the earlier
    `asyncio.create_task(_mapek_worker_bootstrap(), ...)` call referenced
    `asyncio` before its local binding existed. This is exactly the
    deployment configuration the fix exists to make work, so _lifespan()
    itself -- not just _mapek_worker_bootstrap() in isolation -- must be
    driven end to end."""
    monkeypatch.setenv("UASR_MAPEK_ENABLED", "true")
    monkeypatch.setattr(service, "_repair_scheduler", None)
    monkeypatch.setattr(service, "_APPROVAL_TIMEOUT_SECONDS", 0)

    async def _fake_bootstrap():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(service, "_mapek_worker_bootstrap", _fake_bootstrap)

    async with service._lifespan(None):
        assert service._mapek_bootstrap_task is not None
        assert not service._mapek_bootstrap_task.done()

    service._mapek_bootstrap_task = None
