"""
UASR MAPE-K Self-Healing Worker
================================
Polls a Kafka topic, micro-batches events into Parquet, atomically loads them
into DuckDB, and runs the existing DriftDetector + RecoveryLoop on every batch.

The five MAPE-K phases map onto methods of ``MAPEKWorker``:

    Monitor  → ``_monitor_pull_batch``     poll Kafka, build a BatchPayload
    Analyze  → ``_analyze_detect_drift``   run DriftDetector
    Plan     → ``_plan_recovery``          invoke RecoveryLoop on drift
    Execute  → ``_execute_persist`` /      either persist the batch or
               ``_execute_recovery``       pause + run recovery + replay
    Knowledge→ ``_knowledge_update``       update baseline + healing metrics

The "pause consumer → run LLM recovery → restart" requirement is satisfied by
``asyncio.Event``-gated polling: when ``_paused`` is set, the worker stops
calling ``getmany`` but does **not** close the consumer (offsets are
preserved). The recovery deploys a shim into ``RecoveryLoop._deployed_shims``;
on resume, future batches pass through ``loop.apply_shims`` before drift
detection so the same drift never re-fires.

Atomic DuckDB writes: each batch is staged to a Parquet temp file, then
``read_parquet → INSERT`` runs inside a single DuckDB transaction. If the
connection or process dies mid-write the transaction rolls back — readers
never see a partial batch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .drift_detector import DriftDetector
from .metrics import HealingMetricTracker
from .models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
)
from .numeric_heal_controller import HealState, NumericHealController
from .numeric_semantics import (
    NumericSemanticAnalyzer,
    numeric_columns_from_rows,
)
from .recovery_loop import RecoveryLoop, RecoveryLoopConfig

logger = logging.getLogger("uasr.mapek_worker")

# Optional deps — handled lazily so unit tests don't require Kafka / pyarrow / duckdb
try:
    from aiokafka import AIOKafkaConsumer  # type: ignore
    _AIOKAFKA_AVAILABLE = True
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None  # type: ignore[assignment]
    _AIOKAFKA_AVAILABLE = False


# ── Config ────────────────────────────────────────────────────────────

@dataclass
class MAPEKConfig:
    """Configuration for one MAPE-K worker instance."""

    # Kafka
    bootstrap_servers: str = field(default_factory=lambda: os.getenv("UASR_KAFKA_SERVERS", "localhost:9092"))
    topic: str = field(default_factory=lambda: os.getenv("UASR_KAFKA_TOPIC", "aura.uasr.events"))
    group_id: str = field(default_factory=lambda: os.getenv("UASR_KAFKA_GROUP", "aura-uasr-mapek"))
    auto_offset_reset: str = "latest"

    # Source identity & target
    source_id: str = "default"
    duckdb_path: str = field(default_factory=lambda: os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb"))
    table_name: str = "uasr_events"

    # Micro-batching
    batch_size: int = 200
    batch_window_seconds: float = 5.0

    # Parquet staging
    parquet_dir: str = field(default_factory=lambda: os.getenv("UASR_PARQUET_DIR", "data/uasr_parquet"))

    # Recovery
    pause_on_severity: DriftSeverity = DriftSeverity.MEDIUM

    # ── Sprint S18.1 opt-in: Wasserstein-Martingale drift detector ───
    use_martingale_detector: bool = False
    martingale_alpha: float = 0.001
    martingale_baseline_window: int = 100
    martingale_alarm_severity_high: bool = False

    # ── Sprint S18.1c opt-in: Kramer-Magee canary ShimRouter ──────
    # When True, shim deployment uses gradual canary routing instead
    # of the pause/resume mechanism. Batch ingestion continues
    # uninterrupted; new shims receive a small initial traffic share
    # and are promoted or reverted based on drift re-detection.
    use_shim_router: bool = False
    shim_router_canary_initial_weight: float = 0.1

    # ── Phase-1b opt-in: numeric semantic drift channel ───────────
    # When True, each batch's numeric columns are analyzed by the
    # NumericSemanticAnalyzer as a SEPARATE, inference-only channel.
    # It emits a numeric-drift signal (and an un-applied heal proposal
    # for detected unit/scale errors) but NEVER mutates data and NEVER
    # participates in the pause/recovery decision — Phase 1 is
    # observe-only. A per-source baseline is auto-registered from the
    # first ``numeric_baseline_batches`` batches the primary detector
    # deems healthy (no drift). Default OFF.
    use_numeric_semantics: bool = False
    numeric_baseline_batches: int = 15

    # ── Phase-2 opt-in: verified numeric auto-heal ────────────────
    # When True (and ``use_numeric_semantics`` is also True), an accepted
    # heal proposal is routed through the NumericHealController's
    # sequential-verification + two-sided-commit gate. A transform is
    # applied to the batch ONLY after it re-earns acceptance on
    # ``numeric_heal_k_confirm`` consecutive batches, and is auto-reverted
    # once the raw stream is healthy again for ``numeric_heal_revert_patience``
    # batches. A legitimate regime change never passes the gate, so it is
    # alerted but never healed. Every commit/abort/revert writes a
    # SHA-256 audit record. Default OFF — Phase 1b stays observe-only unless
    # this is explicitly enabled.
    numeric_auto_heal: bool = False
    numeric_heal_k_confirm: int = 3
    numeric_heal_revert_patience: int = 3


# ── Progress callback signature ───────────────────────────────────────
# (phase, message, payload) → awaitable
ProgressCb = Callable[[str, str, Dict[str, Any]], Awaitable[None]]


# ── Worker ────────────────────────────────────────────────────────────

class MAPEKWorker:
    """One self-healing pipeline tied to a single Kafka topic + DuckDB table."""

    def __init__(
        self,
        config: MAPEKConfig,
        detector: Optional[DriftDetector] = None,
        recovery_loop: Optional[RecoveryLoop] = None,
        metrics: Optional[HealingMetricTracker] = None,
        progress_cb: Optional[ProgressCb] = None,
        repair_scheduler: Optional[Any] = None,
    ) -> None:
        self._cfg = config
        self._detector = detector or DriftDetector()
        self._loop = recovery_loop or RecoveryLoop(self._detector)
        self._metrics = metrics or HealingMetricTracker()
        self._progress_cb = progress_cb

        # R4 — optional global repair scheduler. When multiple pipelines share
        # one process, injecting a shared ``RepairScheduler`` bounds the total
        # number of concurrent recoveries hitting the shared backend (LLM +
        # sandbox) and admits them in drift-severity priority order. When None
        # (the default, one-worker-per-process), recovery is awaited directly
        # with identical behaviour to before.
        self._repair_scheduler = repair_scheduler

        # Sprint S18.1 — lazily initialise the WassersteinMartingaleDetector
        # ONLY when the flag is on. Keeps the import + state allocation
        # off the hot path for the (default) IQR-only deployment.
        self._martingale: Optional[Any] = None
        if self._cfg.use_martingale_detector:
            from .martingale import WassersteinMartingaleDetector
            self._martingale = WassersteinMartingaleDetector(
                alpha=self._cfg.martingale_alpha,
                baseline_window=self._cfg.martingale_baseline_window,
            )

        # Sprint S18.1c — lazily initialise the ShimRouter when opted in.
        # Canary routing replaces pause/resume for shim deployment:
        # batch ingestion continues; new shims get a fractional traffic
        # share that grows or reverts based on drift re-detection.
        self._shim_router: Optional[Any] = None
        if self._cfg.use_shim_router:
            from .shim_router import ShimRouter
            self._shim_router = ShimRouter()

        # Phase-1b — numeric semantic analyzer (inference-only channel).
        # ``_numeric_healthy_buffer`` accumulates healthy-batch column
        # samples per "source_id:column" key until a baseline is fit.
        self._numeric_analyzer: Optional[NumericSemanticAnalyzer] = None
        self._numeric_healthy_buffer: Dict[str, List[Any]] = {}
        if self._cfg.use_numeric_semantics:
            self._numeric_analyzer = NumericSemanticAnalyzer()

        # Phase-2 — verified numeric auto-heal. Only instantiated when both
        # ``use_numeric_semantics`` and ``numeric_auto_heal`` are on. The
        # controller shares the analyzer's per-source ``NumericBaseline``s
        # (mirrored in on baseline registration) and gates every repair
        # through sequential verification + two-sided commit.
        self._numeric_healer: Optional[NumericHealController] = None
        if self._cfg.use_numeric_semantics and self._cfg.numeric_auto_heal:
            self._numeric_healer = NumericHealController(
                k_confirm=self._cfg.numeric_heal_k_confirm,
                revert_patience=self._cfg.numeric_heal_revert_patience,
            )

        self._consumer: Any = None
        self._duckdb_con: Any = None
        # Whether the sink table has been created this process. Guards against
        # re-running CREATE TABLE on every batch, which reopens a catalog
        # write-write conflict window whenever multiple workers share one
        # DuckDB file (concurrent multi-pipeline healing). See
        # _write_duckdb_atomic.
        self._table_ready: bool = False

        # Lifecycle gates
        self._running = False
        self._paused = asyncio.Event()
        self._paused.set()  # set = NOT paused (consumer may run)
        self._stop_signal = asyncio.Event()

        self._task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        if not _AIOKAFKA_AVAILABLE:
            raise RuntimeError("aiokafka not installed — pip install aiokafka")

        self._consumer = AIOKafkaConsumer(
            self._cfg.topic,
            bootstrap_servers=self._cfg.bootstrap_servers,
            group_id=self._cfg.group_id,
            auto_offset_reset=self._cfg.auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await self._consumer.start()

        self._duckdb_con = self._open_duckdb()
        Path(self._cfg.parquet_dir).mkdir(parents=True, exist_ok=True)

        self._running = True
        self._stop_signal.clear()
        self._task = asyncio.create_task(self._run_forever(), name=f"uasr-mapek-{self._cfg.source_id}")
        await self._emit("started", f"MAPE-K worker online for {self._cfg.topic}", {})

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_signal.set()
        if self._task:
            await asyncio.wait_for(self._task, timeout=15.0)
        if self._consumer:
            await self._consumer.stop()
        if self._duckdb_con is not None:
            try:
                self._duckdb_con.close()
            except Exception as exc:
                logger.debug("duckdb close on shutdown failed: %s", exc)
        await self._emit("stopped", "MAPE-K worker shut down", {})

    def pause(self, reason: str = "") -> None:
        if self._paused.is_set():
            self._paused.clear()
            logger.warning("MAPE-K worker paused: %s", reason or "manual")

    def resume(self) -> None:
        if not self._paused.is_set():
            self._paused.set()
            logger.info("MAPE-K worker resumed")

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    # ── Main loop ─────────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        try:
            while not self._stop_signal.is_set():
                # Pause-gate: blocks here while a recovery is in flight
                await self._paused.wait()

                # ── Monitor ────────────────────────────────────────────
                batch = await self._monitor_pull_batch()
                if batch is None or not batch.rows:
                    continue

                # Apply already-deployed shims so resolved drift doesn't re-fire.
                # S18.1c: when ShimRouter is active, use its weighted
                # canary routing instead of the recovery loop's linear
                # shim-chain application.
                if self._shim_router is not None:
                    try:
                        routed = await self._shim_router.apply(batch.source_id, batch.rows)
                        batch.rows = routed["rows"]
                    except Exception:
                        pass  # no routes yet — use raw rows
                else:
                    healed_rows = self._loop.apply_shims(batch.source_id, batch.rows)
                    batch.rows = healed_rows
                batch.columns = list(batch.rows[0].keys()) if batch.rows else batch.columns

                # ── Analyze ────────────────────────────────────────────
                drift = self._analyze_detect_drift(batch)
                await self._emit(
                    "analyze",
                    f"drift={drift.drift_detected} type={drift.drift_type} severity={drift.severity}",
                    {"batch_id": batch.batch_id, "row_count": len(batch.rows)},
                )

                # Phase-1b — numeric semantic channel (inference-only).
                # Runs AFTER the primary detector and does not influence the
                # pause/recovery decision below. Guarded so a bug here can
                # never take down the loop.
                if self._numeric_analyzer is not None:
                    try:
                        await self._analyze_numeric_semantics(batch, drift)
                    except Exception as exc:  # pragma: no cover
                        logger.debug("numeric-semantics channel skipped: %s", exc)

                if drift.drift_detected and self._should_pause(drift):
                    if self._shim_router is not None:
                        # ── S18.1c: canary-deploy (no pause) ──────────
                        recovery = await self._plan_recovery(drift, batch)
                        if recovery.status == RecoveryStatus.DEPLOYED and recovery.shim:
                            version = f"v_{recovery.recovery_id}"
                            code = recovery.shim.shim_code

                            def _make_transform(c: str):
                                def transform(sid: str, rows):
                                    return RecoveryLoop._sandbox_execute(c, rows)
                                return transform

                            await self._shim_router.add_canary(
                                batch.source_id,
                                version,
                                _make_transform(code),
                                initial_weight=self._cfg.shim_router_canary_initial_weight,
                            )
                            await self._execute_persist(batch)
                            await self._knowledge_update(
                                batch, drift, recovery, batch_healed=False
                            )
                            await self._emit(
                                "canary_deployed",
                                f"shim {version} deployed as canary (weight={self._cfg.shim_router_canary_initial_weight})",
                                {"recovery_id": recovery.recovery_id},
                            )
                        else:
                            await self._emit(
                                "recovery_failed",
                                f"recovery {recovery.recovery_id} status={recovery.status.value}; canary NOT deployed",
                                {"recovery_id": recovery.recovery_id, "drift_event_id": drift.batch_id},
                            )
                    else:
                        # ── Original pause/resume path ────────────────
                        self.pause(reason=f"drift {drift.drift_type}/{drift.severity}")
                        recovery = await self._plan_recovery(drift, batch)

                        if recovery.status == RecoveryStatus.DEPLOYED:
                            healed = self._loop.apply_shims(batch.source_id, batch.rows)
                            batch.rows = healed
                            batch.columns = list(healed[0].keys()) if healed else batch.columns
                            await self._execute_persist(batch)
                            await self._knowledge_update(
                                batch, drift, recovery, batch_healed=True
                            )
                            self.resume()
                        else:
                            await self._emit(
                                "recovery_failed",
                                f"recovery {recovery.recovery_id} status={recovery.status.value}; consumer remains paused",
                                {"recovery_id": recovery.recovery_id, "drift_event_id": drift.batch_id},
                            )
                            await self._stop_signal.wait()
                            break
                else:
                    # ── Execute (happy path) ───────────────────────────
                    await self._execute_persist(batch)
                    await self._knowledge_update(batch, drift, None)

                await self._commit_offsets()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MAPE-K worker crashed")
            raise

    # ── Monitor ───────────────────────────────────────────────────────

    async def _monitor_pull_batch(self) -> Optional[BatchPayload]:
        """Poll Kafka for ``batch_size`` events or until ``batch_window_seconds`` elapses."""
        rows: List[Dict[str, Any]] = []
        deadline = time.monotonic() + self._cfg.batch_window_seconds

        while len(rows) < self._cfg.batch_size and time.monotonic() < deadline:
            remaining_ms = max(50, int((deadline - time.monotonic()) * 1000))
            try:
                msg_pack = await self._consumer.getmany(
                    timeout_ms=remaining_ms,
                    max_records=self._cfg.batch_size - len(rows),
                )
            except Exception as exc:
                logger.warning("Kafka getmany failed: %s", exc)
                return None

            for tp_msgs in msg_pack.values():
                for m in tp_msgs:
                    if isinstance(m.value, dict):
                        rows.append(m.value)

        if not rows:
            return None

        cols = sorted({k for r in rows for k in r.keys()})
        return BatchPayload(
            source_id=self._cfg.source_id,
            batch_id=uuid.uuid4().hex[:12],
            columns=cols,
            rows=rows,
            schema_snapshot={c: type(rows[0].get(c)).__name__ for c in cols if rows[0].get(c) is not None},
        )

    async def _commit_offsets(self) -> None:
        try:
            await self._consumer.commit()
        except Exception as exc:
            logger.warning("Kafka commit failed: %s", exc)

    # ── Analyze ───────────────────────────────────────────────────────

    def _analyze_detect_drift(self, batch: BatchPayload) -> DriftDetectionResult:
        """Run the configured drift detector.

        Sprint S18.1 dispatch: when ``cfg.use_martingale_detector`` is
        True, the Wasserstein-Martingale detector runs alongside the
        classical IQR/KL detector and FAILS-OPEN to it on any column
        without a registered baseline. This way:

        * If a martingale alarm fires (provable α false-positive bound),
          we report it as the primary drift signal — the operator gets
          a mathematically-guaranteed-real drift.
        * If no martingale alarm but the classical detector fires
          (schema drift, large KL, semantic shift), we still report
          it. The classical signals catch things the martingale
          doesn't (schema additions, embedding cosine).

        Default OFF — `cfg.use_martingale_detector=False` skips the
        martingale path entirely and the function reduces to the
        pre-S18.1 single-line `self._detector.detect(batch)`.
        """
        if self._cfg.use_martingale_detector and self._martingale is not None:
            martingale_result = self._analyze_martingale(batch)
            if martingale_result is not None and martingale_result.drift_detected:
                return martingale_result
        return self._detector.detect(batch)

    def _analyze_martingale(
        self, batch: BatchPayload,
    ) -> Optional[DriftDetectionResult]:
        """Per-column Wasserstein-Martingale update; build the same
        ``DriftDetectionResult`` shape the classical detector returns.

        Returns None when no column in this batch has a registered
        baseline (martingale can't fire) — caller falls through to
        the classical detector. Returns a result with ``drift_detected
        = True`` only when at least one column's martingale crossed
        the Azuma-Hoeffding bound.
        """
        assert self._martingale is not None
        alarms: List[str] = []
        max_distance: float = 0.0
        for col in batch.columns:
            # Extract numeric samples from this column. Skip strings,
            # None, dicts — only Wasserstein-comparable values.
            samples: List[float] = []
            for r in batch.rows:
                v = r.get(col)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    samples.append(float(v))
            if not samples:
                continue
            try:
                fired = self._martingale.update(batch.source_id, col, samples)
            except Exception as exc:
                logger.warning("martingale update failed for col=%s: %s", col, exc)
                continue
            diag = self._martingale.diagnostics(batch.source_id, col)
            if diag.get("last_distance") is not None:
                max_distance = max(max_distance, float(diag["last_distance"]))
            if fired:
                alarms.append(col)

        if not alarms:
            return None

        severity = (
            DriftSeverity.HIGH
            if self._cfg.martingale_alarm_severity_high
            else DriftSeverity.MEDIUM
        )
        return DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
            drift_detected=True,
            drift_type=DriftType.STATISTICAL,
            severity=severity,
            affected_columns=alarms,
            kl_divergence=None,
            drift_vector={
                "detector": "wasserstein_martingale",
                "alpha": self._cfg.martingale_alpha,
                "alarms": alarms,
                "max_wasserstein_1": max_distance,
            },
            details=(
                f"Wasserstein-Martingale alarm on columns {alarms!r} "
                f"(α={self._cfg.martingale_alpha}, max W₁={max_distance:.4f})"
            ),
        )

    def _should_pause(self, drift: DriftDetectionResult) -> bool:
        if not drift.drift_detected or drift.severity is None:
            return False
        order = [DriftSeverity.LOW, DriftSeverity.MEDIUM, DriftSeverity.HIGH, DriftSeverity.CRITICAL]
        return order.index(drift.severity) >= order.index(self._cfg.pause_on_severity)

    # ── Plan ──────────────────────────────────────────────────────────

    async def _plan_recovery(
        self,
        drift: DriftDetectionResult,
        batch: BatchPayload,
    ) -> RecoveryLoopResult:
        await self._emit(
            "plan",
            f"invoking LLM recovery for {drift.drift_type} drift on {drift.affected_columns}",
            {"batch_id": batch.batch_id},
        )
        if self._repair_scheduler is not None:
            # Submit through the global scheduler: bounded concurrency + severity
            # priority across all co-resident pipelines. ``submit`` awaits the
            # same coroutine, so the result/exception contract is unchanged.
            return await self._repair_scheduler.submit(
                batch.source_id,
                drift.severity,
                lambda: self._loop.run(drift, batch),
            )
        return await self._loop.run(drift, batch)

    # ── Execute ───────────────────────────────────────────────────────

    async def _execute_persist(self, batch: BatchPayload) -> None:
        """Atomic write: rows → Parquet temp file → DuckDB INSERT inside a txn."""
        path = await asyncio.to_thread(self._write_parquet, batch)
        await asyncio.to_thread(self._write_duckdb_atomic, path)
        await self._emit(
            "execute",
            f"persisted {len(batch.rows)} rows → {self._cfg.table_name}",
            {"parquet_path": path, "row_count": len(batch.rows)},
        )

    def _write_parquet(self, batch: BatchPayload) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(batch.rows)

        # Write to a temp file in the same dir, then atomic rename — readers
        # of the directory never see a half-written file.
        target_dir = Path(self._cfg.parquet_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / f"{batch.source_id}-{batch.batch_id}.parquet"
        with tempfile.NamedTemporaryFile(
            dir=str(target_dir), suffix=".parquet.tmp", delete=False
        ) as tmp:
            tmp_path = tmp.name
        pq.write_table(table, tmp_path, compression="snappy")
        os.replace(tmp_path, final)  # atomic on POSIX & Windows
        return str(final)

    def _write_duckdb_atomic(self, parquet_path: str) -> None:
        con = self._duckdb_con
        tbl = self._cfg.table_name
        # Multi-pipeline correctness: DuckDB uses optimistic concurrency
        # control, so running ``CREATE TABLE IF NOT EXISTS`` on *every* batch
        # reopens a catalog write-write conflict window each time multiple
        # workers share one DuckDB file — and a losing worker's transaction
        # aborts, silently dropping the batch (~3% loss measured at 4–16
        # concurrent writers). Concurrent INSERTs into an *existing* table do
        # not conflict. So we create the table at most once per process
        # (``_table_ready``), then INSERT-only, and retry with a small jittered
        # backoff if the first-batch CREATE races another worker.
        # Retry budget is generous because a write-write conflict is transient
        # (another worker committed first) and losing a batch is a correctness
        # failure, not just a latency hit. Exponential backoff capped at ~50ms
        # keeps tail latency bounded even under heavy multi-pipeline contention.
        max_attempts = 12
        for attempt in range(max_attempts):
            con.execute("BEGIN TRANSACTION")
            try:
                if not self._table_ready:
                    con.execute(
                        f'CREATE TABLE IF NOT EXISTS "{tbl}" AS '
                        f"SELECT * FROM read_parquet(?) WHERE 1=0",
                        [parquet_path],
                    )
                con.execute(
                    f'INSERT INTO "{tbl}" BY NAME SELECT * FROM read_parquet(?)',
                    [parquet_path],
                )
                con.execute("COMMIT")
                self._table_ready = True
                return
            except Exception as exc:
                try:
                    con.execute("ROLLBACK")
                except Exception:  # pragma: no cover — rollback on dead txn
                    pass
                # A catalog/transaction write-write conflict means another
                # worker committed first; the table now exists, so skip CREATE
                # and retry the INSERT. Any other error is a real failure.
                if "conflict" in str(exc).lower() and attempt < max_attempts - 1:
                    self._table_ready = True
                    backoff = min(0.05, 0.002 * (2 ** attempt))
                    time.sleep(backoff + random.random() * 0.002)
                    continue
                raise

    # ── Knowledge ─────────────────────────────────────────────────────

    async def _analyze_numeric_semantics(
        self, batch: BatchPayload, drift: DriftDetectionResult,
    ) -> None:
        """Phase-1b numeric semantic channel — INFERENCE ONLY.

        For each numeric column: if a per-source baseline exists, analyze it and
        emit a numeric-drift signal (plus an un-applied heal *proposal* for
        detected unit/scale errors). Otherwise accumulate healthy-batch samples
        (only when the primary detector saw no drift) and fit a baseline once
        ``numeric_baseline_batches`` have been collected.

        This never mutates ``batch`` and never feeds the pause/recovery decision.
        The proposal it emits is for an operator/downstream gate to act on, not
        for this worker to apply.
        """
        analyzer = self._numeric_analyzer
        if analyzer is None:
            return
        columns = numeric_columns_from_rows(batch.rows)
        for col_name, values in columns.items():
            key = f"{batch.source_id}:{col_name}"
            if analyzer.has_baseline(key):
                # Phase-2 — run the verified auto-heal gate on the RAW column.
                # Runs on every batch (not only drifted ones) so a committed
                # transform's two-sided revert check sees healthy batches too.
                if self._numeric_healer is not None:
                    await self._auto_heal_column(batch, col_name, values)
                sig = analyzer.analyze_column(values, key=key, column_name=col_name)
                if sig.drifted:
                    proposal = sig.proposal
                    payload: Dict[str, Any] = {
                        "batch_id": batch.batch_id,
                        "source_id": batch.source_id,
                        "column": col_name,
                        "tier": sig.tier,
                        "z_distance": round(sig.z_distance, 4),
                    }
                    if proposal is not None and proposal.transform != "none":
                        payload["proposed_transform"] = proposal.transform
                        payload["proposed_factor"] = proposal.factor
                        payload["confidence"] = round(proposal.confidence, 4)
                        msg = (
                            f"numeric drift on '{col_name}': proposed "
                            f"{proposal.transform} (conf={proposal.confidence:.2f}) "
                            f"— NOT applied"
                        )
                    else:
                        msg = (
                            f"numeric drift on '{col_name}': no inverse transform "
                            f"clears the gate (likely regime change) — alert only"
                        )
                    await self._emit("numeric_semantics", msg, payload)
            elif not drift.drift_detected:
                # Accumulate only demonstrably-healthy batches into the baseline.
                buf = self._numeric_healthy_buffer.setdefault(key, [])
                buf.append(list(values))
                if len(buf) >= self._cfg.numeric_baseline_batches:
                    analyzer.register_baseline(key, buf)
                    self._numeric_healthy_buffer.pop(key, None)
                    # Phase-2 — mirror the freshly-fit baseline into the healer
                    # so its gate has the same reference to score against.
                    if self._numeric_healer is not None:
                        bl = analyzer.get_baseline(key)
                        if bl is not None:
                            self._numeric_healer.load_baseline(
                                batch.source_id, col_name, bl,
                            )
                    await self._emit(
                        "numeric_semantics",
                        f"baseline registered for '{col_name}' "
                        f"({self._cfg.numeric_baseline_batches} healthy batches)",
                        {"source_id": batch.source_id, "column": col_name},
                    )

    async def _auto_heal_column(
        self, batch: BatchPayload, col_name: str, values: List[Any],
    ) -> None:
        """Phase-2 — advance the verified auto-heal gate for one column.

        Observes the RAW column, emits an audit event on any state transition
        (canary_open / commit / abort / revert), and — when the column is
        COMMITTED — writes the healed values back into ``batch.rows`` so the
        corrected data flows downstream. Only cells whose value coerces to float
        are rewritten; nulls and non-numeric cells are left untouched.
        """
        healer = self._numeric_healer
        if healer is None:
            return
        decision = healer.observe(batch.source_id, col_name, values)
        if decision.audit is not None:
            a = decision.audit
            await self._emit(
                "numeric_heal",
                f"{a.event} on '{col_name}': transform={a.transform} "
                f"factor={a.factor}",
                {
                    "batch_id": batch.batch_id,
                    "source_id": batch.source_id,
                    "column": col_name,
                    "event": a.event,
                    "transform": a.transform,
                    "factor": a.factor,
                    "state": decision.state.value,
                    "record_id": a.record_id,
                    "audit_record_hash": a.audit_record_hash,
                },
            )
        # Apply the committed transform to the batch in place.
        if decision.state is HealState.COMMITTED and decision.applied_factor != 1.0:
            factor = decision.applied_factor
            for row in batch.rows:
                if col_name not in row or row[col_name] is None:
                    continue
                try:
                    row[col_name] = float(row[col_name]) * factor
                except (TypeError, ValueError):
                    continue

    async def _knowledge_update(
        self,
        batch: BatchPayload,
        drift: DriftDetectionResult,
        recovery: Optional[RecoveryLoopResult],
        batch_healed: bool = False,
    ) -> None:
        # Re-baseline ONLY when we are handed a confirmed post-shim batch
        # (``batch_healed=True``).  Two hazards this guards against:
        #
        #   1. Baseline poisoning — in the canary-deploy path the persisted
        #      ``batch`` is the *raw drifted* data (the shim runs at low weight
        #      in the router, not inline), so snapshotting it would register
        #      drift as the new normal and mask subsequent real drift.  The
        #      statistical baseline should advance when a shim is *promoted*
        #      from canary to primary, not at canary-deploy time.
        #   2. Channel clobbering — registering via the ``BatchPayload`` path
        #      recomputes and overwrites the schema and reference-embedding
        #      baselines every cycle (baseline creep).  We instead snapshot
        #      only the statistical distributions via the dict path, leaving
        #      the schema and semantic reference baselines intact.
        if (
            recovery is not None
            and recovery.status == RecoveryStatus.DEPLOYED
            and batch_healed
        ):
            dists = self._detector._compute_distributions(batch)
            if dists:
                self._detector.register_baseline(batch.source_id, dists)

        # Feed the healing tracker so /uasr/metrics dashboards update.
        # Only emit on actual recovery cycles — happy-path batches without drift
        # don't have a "recovery event" to record (no shim, no latency).
        if recovery is not None:
            try:
                self._metrics.record_from_loop_result(batch.source_id, recovery)
            except Exception as exc:
                logger.debug("Healing metrics record skipped: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────

    def _open_duckdb(self) -> Any:
        import duckdb
        Path(self._cfg.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(self._cfg.duckdb_path)

    async def _emit(self, phase: str, message: str, payload: Dict[str, Any]) -> None:
        logger.info("[mapek:%s] %s | %s", phase, message, payload)
        if self._progress_cb:
            try:
                await self._progress_cb(phase, message, payload)
            except Exception as exc:
                logger.debug("progress_cb failed: %s", exc)


# ── CLI entry point ───────────────────────────────────────────────────

async def _main() -> None:
    """Stand-alone runner: ``python -m uasr.mapek_worker``."""
    logging.basicConfig(level=os.getenv("UASR_LOG_LEVEL", "INFO"))
    cfg = MAPEKConfig()
    worker = MAPEKWorker(cfg)
    await worker.start()
    # Wait on an Event that never fires — sleeps until SIGINT/cancel
    # without a periodic wakeup. The previous ``while True: sleep(3600)``
    # would wake every hour even when nothing changed (Ruff ASYNC110).
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(_main())
