"""
DAR background daemon.

Round-robins through tables in the DuckDB analytics lake; on each tick
it picks one table, runs the LangGraph DAG, and sleeps. State is held
in-memory only (next-table index, last-tick timestamp) — restarts
restart the rotation, which is fine because findings persist to the
metadata DB and dedupe is the UI's job.

Lifecycle mirrors ``uasr.mapek_worker.MAPEKWorker``: ``asyncio.Event``
gating, opt-in via env, never crashes the lifespan if the lake is
empty or unreachable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .graph import run_dar
from .schemas import DARState

logger = logging.getLogger("aura.dar.daemon")


@dataclass
class DARDaemonConfig:
    duckdb_path: str = field(default_factory=lambda: os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb"))
    interval_seconds: int = field(default_factory=lambda: int(os.getenv("AURA_DAR_INTERVAL_S", "1800")))
    source_id: str = field(default_factory=lambda: os.getenv("AURA_DAR_SOURCE_ID", "duckdb_lake"))
    # Tables to skip — system / ephemeral noise. Operator-tunable.
    skip_prefixes: List[str] = field(default_factory=lambda: ["__", "tmp_", "_aura_"])


class DARDaemon:
    def __init__(self, config: Optional[DARDaemonConfig] = None) -> None:
        self._cfg = config or DARDaemonConfig()
        self._running = False
        self._stop_signal = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        # Round-robin cursor: refreshed each tick from the live table list
        self._next_index = 0
        self._last_run: Optional[dict] = None
        self._last_table: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_signal.clear()
        self._task = asyncio.create_task(self._loop(), name="dar-daemon")
        logger.info("DAR daemon started (interval=%ds, lake=%s)",
                    self._cfg.interval_seconds, self._cfg.duckdb_path)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_signal.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("DAR daemon stop() timed out")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Status (consumed by /dar/daemon/status) ───────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "config": {
                "duckdb_path": self._cfg.duckdb_path,
                "interval_seconds": self._cfg.interval_seconds,
                "source_id": self._cfg.source_id,
            },
            "next_table_index": self._next_index,
            "last_table": self._last_table,
            "last_run": self._last_run,
        }

    # ── Main loop ─────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while not self._stop_signal.is_set():
                await self._tick()
                # Interruptible sleep
                try:
                    await asyncio.wait_for(self._stop_signal.wait(),
                                           timeout=self._cfg.interval_seconds)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("DAR daemon crashed")
            raise

    async def _tick(self) -> None:
        try:
            tables = await asyncio.to_thread(self._list_tables)
        except Exception as exc:
            logger.warning("DAR tick: list_tables failed (%s) — skipping", exc)
            return
        if not tables:
            logger.debug("DAR tick: no tables in lake yet")
            return

        idx = self._next_index % len(tables)
        table = tables[idx]
        self._next_index = idx + 1
        self._last_table = table

        run_id = uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        try:
            state: DARState = await run_dar(
                source_id=self._cfg.source_id,
                table_name=table,
                duckdb_path=self._cfg.duckdb_path,
                run_id=run_id,
            )
            self._last_run = {
                "run_id": run_id,
                "table": table,
                "duration_ms": (time.perf_counter() - t0) * 1000,
                "completed_nodes": state.completed_nodes,
                "findings": len(state.findings),
                "persisted": len(state.persisted_ids),
                "errors": [{"node": e.node, "message": e.message} for e in state.errors],
            }
            logger.info("DAR tick: table=%s findings=%d persisted=%d",
                        table, len(state.findings), len(state.persisted_ids))
        except Exception as exc:
            logger.warning("DAR tick: run failed for table=%s — %s", table, exc)
            self._last_run = {"run_id": run_id, "table": table, "error": str(exc)}

    def _list_tables(self) -> List[str]:
        """Sync DuckDB introspection — runs on a worker thread."""
        import duckdb
        if not os.path.exists(self._cfg.duckdb_path):
            return []
        con = duckdb.connect(self._cfg.duckdb_path, read_only=True)
        try:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
        finally:
            con.close()
        out = []
        for (name,) in rows:
            if any(name.startswith(p) for p in self._cfg.skip_prefixes):
                continue
            out.append(name)
        return out
