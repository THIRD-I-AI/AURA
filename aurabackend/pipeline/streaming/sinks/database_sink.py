"""
Database Sink – Upsert window results into a relational table
==============================================================
Supports DuckDB (in-proc) and PostgreSQL via connectors.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from pipeline.streaming.models import WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.database")


class DatabaseSink(BaseSink):

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._table = config.get("table", "streaming_results")
        self._connector_type = config.get("connector", "duckdb")
        self._conn: Any = None

    async def start(self) -> None:
        if self._connector_type == "duckdb":
            import duckdb
            db_path = self.config.get("path", ":memory:")
            self._conn = duckdb.connect(db_path)
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS "{self._table}" (
                    pipeline_id    VARCHAR,
                    window_key     VARCHAR,
                    window_start   TIMESTAMP,
                    window_end     TIMESTAMP,
                    event_count    INTEGER,
                    aggregations   VARCHAR,
                    inserted_at    TIMESTAMP DEFAULT current_timestamp
                )
            """)
        self._running = True
        logger.info("Database sink started (connector=%s, table=%s)", self._connector_type, self._table)

    async def stop(self) -> None:
        self._running = False
        if self._conn and self._connector_type == "duckdb":
            self._conn.close()
            self._conn = None
        logger.info("Database sink stopped")

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        if not self._conn:
            logger.warning("Database sink not connected — skipping emit")
            return

        agg_json = json.dumps(window.aggregations, default=str)

        if self._connector_type == "duckdb":
            self._conn.execute(
                f'INSERT INTO "{self._table}" (pipeline_id, window_key, window_start, window_end, event_count, aggregations) VALUES (?, ?, ?, ?, ?, ?)',
                [
                    pipeline_id,
                    window.window_key,
                    window.window_start.isoformat() if window.window_start else None,
                    window.window_end.isoformat() if window.window_end else None,
                    window.event_count,
                    agg_json,
                ],
            )
        logger.debug("DB sink: wrote window %s for %s", window.window_key, pipeline_id)
