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
    RecoveryLoopResult,
    RecoveryStatus,
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
    ) -> None:
        self._cfg = config
        self._detector = detector or DriftDetector()
        self._loop = recovery_loop or RecoveryLoop(self._detector)
        self._metrics = metrics or HealingMetricTracker()
        self._progress_cb = progress_cb

        self._consumer: Any = None
        self._duckdb_con: Any = None

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
            except Exception:
                pass
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

                # Apply already-deployed shims so resolved drift doesn't re-fire
                healed_rows = self._loop.apply_shims(batch.source_id, batch.rows)
                batch.rows = healed_rows
                batch.columns = list(healed_rows[0].keys()) if healed_rows else batch.columns

                # ── Analyze ────────────────────────────────────────────
                drift = self._analyze_detect_drift(batch)
                await self._emit(
                    "analyze",
                    f"drift={drift.drift_detected} type={drift.drift_type} severity={drift.severity}",
                    {"batch_id": batch.batch_id, "row_count": len(batch.rows)},
                )

                if drift.drift_detected and self._should_pause(drift):
                    # ── Plan + Execute (recovery path) ─────────────────
                    self.pause(reason=f"drift {drift.drift_type}/{drift.severity}")
                    recovery = await self._plan_recovery(drift, batch)

                    if recovery.status == RecoveryStatus.DEPLOYED:
                        # Re-apply healed batch and persist
                        healed = self._loop.apply_shims(batch.source_id, batch.rows)
                        batch.rows = healed
                        batch.columns = list(healed[0].keys()) if healed else batch.columns
                        await self._execute_persist(batch)
                        await self._knowledge_update(batch, drift, recovery)
                        self.resume()
                    else:
                        # Recovery failed — stay paused, surface for human triage
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
        return self._detector.detect(batch)

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
        # CREATE-IF-NOT-EXISTS off the parquet schema, then INSERT inside a
        # transaction. DuckDB rolls back on connection loss / process kill.
        con.execute("BEGIN TRANSACTION")
        try:
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
        except Exception:
            con.execute("ROLLBACK")
            raise

    # ── Knowledge ─────────────────────────────────────────────────────

    async def _knowledge_update(
        self,
        batch: BatchPayload,
        drift: DriftDetectionResult,
        recovery: Optional[RecoveryLoopResult],
    ) -> None:
        # If recovery succeeded, the post-shim batch is the new normal —
        # snapshot its distributions so the next batch's drift detection
        # compares against the corrected baseline rather than the stale one.
        if recovery and recovery.status == RecoveryStatus.DEPLOYED:
            self._detector.register_baseline(batch.source_id, batch)

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
    try:
        # Run until SIGINT
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(_main())
