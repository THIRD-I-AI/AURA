"""R4 integration — MAPEKWorker recovery routed through a shared RepairScheduler.

Proves that when several co-resident workers share one ``RepairScheduler``,
their recoveries are (a) bounded to the scheduler's concurrency cap and
(b) admitted in drift-severity priority order — while the default
(no scheduler) path is unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
)
from uasr.repair_scheduler import RepairScheduler


def _drift(severity: DriftSeverity, source_id: str = "s0") -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id=source_id,
        drift_detected=True,
        drift_type=DriftType.STATISTICAL,
        severity=severity,
        affected_columns=["value"],
        batch_id="b1",
    )


def _batch(source_id: str) -> BatchPayload:
    return BatchPayload(source_id=source_id, batch_id="b1", rows=[{"value": 1.0}])


@pytest.mark.asyncio
async def test_plan_recovery_uses_scheduler_and_is_bounded():
    """Recoveries from many workers respect the shared concurrency cap."""
    sched = RepairScheduler(max_concurrent=2)
    await sched.start()

    inflight = {"cur": 0, "max": 0}

    async def fake_run(drift, batch):
        inflight["cur"] += 1
        inflight["max"] = max(inflight["max"], inflight["cur"])
        await asyncio.sleep(0.02)
        inflight["cur"] -= 1
        return RecoveryLoopResult(
            drift_event_id=batch.batch_id,
            recovery_id=f"r_{batch.source_id}",
            status=RecoveryStatus.DEPLOYED,
            total_latency_seconds=0.02,
        )

    workers = []
    for i in range(8):
        w = MAPEKWorker(config=MAPEKConfig(source_id=f"s{i}"), repair_scheduler=sched)
        w._loop.run = fake_run  # stub the expensive recovery
        workers.append(w)

    results = await asyncio.gather(
        *[w._plan_recovery(_drift(DriftSeverity.HIGH), _batch(f"s{i}")) for i, w in enumerate(workers)]
    )
    await sched.stop()

    assert all(r.status == RecoveryStatus.DEPLOYED for r in results)
    assert inflight["max"] <= 2  # never exceeded the cap
    assert sched.stats.completed == 8


@pytest.mark.asyncio
async def test_plan_recovery_priority_across_pipelines():
    """A CRITICAL pipeline's recovery is admitted before a LOW backlog."""
    sched = RepairScheduler(max_concurrent=1)
    await sched.start()
    admitted = []

    async def fake_run(drift, batch):
        admitted.append((batch.source_id, drift.severity.value))
        await asyncio.sleep(0.005)
        return RecoveryLoopResult(
            drift_event_id=batch.batch_id,
            recovery_id=f"r_{batch.source_id}",
            status=RecoveryStatus.DEPLOYED,
            total_latency_seconds=0.005,
        )

    def mkworker(sid):
        w = MAPEKWorker(config=MAPEKConfig(source_id=sid), repair_scheduler=sched)
        w._loop.run = fake_run
        return w

    # Occupy the slot with a LOW recovery, then flood mixed severities behind it.
    occ = mkworker("occ")
    occ_task = asyncio.create_task(occ._plan_recovery(_drift(DriftSeverity.LOW), _batch("occ")))
    await asyncio.sleep(0.002)

    backlog = [
        mkworker("low1")._plan_recovery(_drift(DriftSeverity.LOW), _batch("low1")),
        mkworker("crit1")._plan_recovery(_drift(DriftSeverity.CRITICAL), _batch("crit1")),
        mkworker("high1")._plan_recovery(_drift(DriftSeverity.HIGH), _batch("high1")),
    ]
    tasks = [asyncio.create_task(c) for c in backlog]
    await asyncio.gather(occ_task, *tasks)
    await sched.stop()

    assert admitted[0][0] == "occ"
    # Backlog drains in priority order: CRITICAL, HIGH, LOW.
    assert [a[1] for a in admitted[1:]] == ["critical", "high", "low"]


@pytest.mark.asyncio
async def test_no_scheduler_is_unchanged():
    """Without a scheduler, recovery is awaited directly (default behaviour)."""
    called = {"n": 0}

    async def fake_run(drift, batch):
        called["n"] += 1
        return RecoveryLoopResult(
            drift_event_id=batch.batch_id,
            recovery_id="r",
            status=RecoveryStatus.DEPLOYED,
            total_latency_seconds=0.0,
        )

    w = MAPEKWorker(config=MAPEKConfig(source_id="s0"))  # no scheduler
    w._loop.run = fake_run
    assert w._repair_scheduler is None
    r = await w._plan_recovery(_drift(DriftSeverity.HIGH), _batch("s0"))
    assert r.status == RecoveryStatus.DEPLOYED
    assert called["n"] == 1
