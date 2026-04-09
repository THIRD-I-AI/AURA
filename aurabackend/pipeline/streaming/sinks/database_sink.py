"""
Database Sink – Upsert window results into a relational table
==============================================================
Supports DuckDB (in-proc) and PostgreSQL via asyncpg.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from pipeline.streaming.models import WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.database")

_PG_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS "{table}" (
    pipeline_id    VARCHAR(255),
    window_key     VARCHAR(512),
    window_start   TIMESTAMP,
    window_end     TIMESTAMP,
    event_count    INTEGER,
    aggregations   JSONB,
    inserted_at    TIMESTAMP DEFAULT NOW()
)
"""

_PG_INSERT = """
INSERT INTO "{table}" (pipeline_id, window_key, window_start, window_end, event_count, aggregations)
VALUES ($1, $2, $3, $4, $5, $6::jsonb)
"""


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
        elif self._connector_type == "postgresql":
            try:
                import asyncpg
            except ImportError:
                raise ImportError(
                    "asyncpg is required for PostgreSQL DatabaseSink. "
                    "Install it with: pip install asyncpg"
                )
            dsn = self.config.get("dsn")
            if dsn:
                self._conn = await asyncpg.connect(dsn)
            else:
                self._conn = await asyncpg.connect(
                    host=self.config.get("host", "localhost"),
                    port=int(self.config.get("port", 5432)),
                    database=self.config.get("database", "aura"),
                    user=self.config.get("user", "postgres"),
                    password=self.config.get("password", ""),
                )
            await self._conn.execute(
                _PG_CREATE_TABLE.format(table=self._table)
            )
        self._running = True
        logger.info("Database sink started (connector=%s, table=%s)", self._connector_type, self._table)

    async def stop(self) -> None:
        self._running = False
        if self._conn and self._connector_type == "duckdb":
            self._conn.close()
            self._conn = None
        elif self._conn and self._connector_type == "postgresql":
            await self._conn.close()
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
                    datetime.fromtimestamp(window.window_start, tz=timezone.utc) if isinstance(window.window_start, (int, float)) else window.window_start,
                    datetime.fromtimestamp(window.window_end, tz=timezone.utc) if isinstance(window.window_end, (int, float)) and window.window_end != float("inf") else None,
                    window.event_count,
                    agg_json,
                ],
            )
        elif self._connector_type == "postgresql":
            ws_start = (
                datetime.fromtimestamp(window.window_start, tz=timezone.utc)
                if isinstance(window.window_start, (int, float))
                else window.window_start
            )
            ws_end = (
                datetime.fromtimestamp(window.window_end, tz=timezone.utc)
                if isinstance(window.window_end, (int, float)) and window.window_end != float("inf")
                else None
            )
            await self._conn.execute(
                _PG_INSERT.format(table=self._table),
                pipeline_id,
                window.window_key,
                ws_start,
                ws_end,
                window.event_count,
                agg_json,
            )
        logger.debug("DB sink: wrote window %s for %s", window.window_key, pipeline_id)
