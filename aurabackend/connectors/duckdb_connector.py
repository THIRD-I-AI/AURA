"""
DuckDB connector for AURA — fast local analytics
Queries CSV, Parquet, JSON, Excel files directly via SQL.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base import BaseConnector, ConnectorConfig

logger = logging.getLogger("aura.connectors.duckdb")


class DuckDBConnector(BaseConnector):
    """In-process analytical database — no server needed.

    Sprint 17: optionally loads the DuckDB ``spatial`` extension when
    the connector config requests it. Spatial enablement is opt-in
    because the extension adds ~5MB of compiled GEOS bindings and
    fails-closed when the install can't reach the DuckDB extension
    server (offline / air-gapped deployments). When enabled, the
    connector reports ``spatial=True`` in capabilities() and accepts
    PostGIS-style queries via spatial_query()."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._conn: Any = None
        self._spatial_loaded: bool = False

    async def connect(self) -> bool:
        try:
            import duckdb  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("DuckDBConnector: duckdb is not installed")
            return False
        try:
            path = (
                self.config.connection_string
                or (self.config.extra_params or {}).get("db_path", ":memory:")
            )
            self._conn = duckdb.connect(str(path))
            self._is_connected = True
            self.metadata.connected = True
            # Sprint 17: opt-in spatial extension. Triggered either by
            # the SourceType.DUCKDB_SPATIAL enum entry or by an explicit
            # extra_params={"enable_spatial": True} on a plain
            # SourceType.DUCKDB connector. Failures are non-fatal —
            # the SQL surface still works without spatial.
            extra = self.config.extra_params or {}
            wants_spatial = (
                self.config.source_type.value == "duckdb_spatial"
                or bool(extra.get("enable_spatial", False))
            )
            if wants_spatial:
                self._spatial_loaded = self._try_load_spatial()
                if not self._spatial_loaded:
                    logger.warning(
                        "DuckDBConnector: spatial extension requested but "
                        "could not be loaded (offline install? missing "
                        "extension server?). SQL surface still works."
                    )
            return True
        except Exception as exc:
            logger.warning("DuckDB connection failed: %s", exc)
            return False

    def _try_load_spatial(self) -> bool:
        """Install + load the DuckDB spatial extension. Returns True on
        success, False on any failure (no exception leaks out — the SQL
        surface is unaffected). Wrapped in two stages because INSTALL
        may be a no-op if the extension is already on disk."""
        if self._conn is None:
            return False
        try:
            self._conn.execute("INSTALL spatial")
            self._conn.execute("LOAD spatial")
            return True
        except Exception as exc:
            logger.warning("DuckDB spatial load failed: %s", exc)
            return False

    async def disconnect(self) -> bool:
        if self._conn:
            self._conn.close()
            self._conn = None
        self._is_connected = False
        self.metadata.connected = False
        return True

    async def list_tables(self) -> List[str]:
        if not self._conn:
            return []
        try:
            result = self._conn.execute("SHOW TABLES")
            tables = [row[0] for row in result.fetchall()]
            self.metadata.table_count = len(tables)
            return tables
        except Exception as exc:
            logger.warning("DuckDB list_tables failed: %s", exc)
            return []

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        if not self._conn:
            return {}
        try:
            result = self._conn.execute(f"DESCRIBE {table_name}")
            cols = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return {
                "table_name": table_name,
                "columns": [
                    {
                        "name": dict(zip(cols, row)).get("column_name", row[0]),
                        "type": dict(zip(cols, row)).get("column_type", row[1]),
                        "nullable": True,
                    }
                    for row in rows
                ],
            }
        except Exception as exc:
            logger.warning("DuckDB get_table_schema failed: %s", exc)
            return {}

    async def sample_rows(self, table_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        return await self.execute_query(f"SELECT * FROM {table_name} LIMIT {limit}")

    async def execute_query(self, query: str, limit: int = 1000) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        try:
            result = self._conn.execute(query)
            cols = [desc[0] for desc in result.description]
            return [dict(zip(cols, row)) for row in result.fetchmany(limit)]
        except Exception as exc:
            logger.warning("DuckDB query failed: %s", exc)
            return []

    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        if not self._conn:
            return {}
        try:
            schema = await self.get_table_schema(table_name)
            count_result = self._conn.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = count_result.fetchone()[0]
            return {
                "table_name": table_name,
                "rows": row_count,
                "columns": len(schema.get("columns", [])),
                "columns_profile": {},
            }
        except Exception as exc:
            logger.warning("DuckDB profile failed: %s", exc)
            return {}

    # ---- DuckDB-specific: direct file queries ----

    async def query_file(self, file_path: str, query: str = "", limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Query a CSV/Parquet/JSON file directly without loading it into a table.
        If ``query`` is empty, returns ``SELECT * FROM '<file_path>' LIMIT <limit>``.
        """
        if not self._conn:
            return []
        if not query:
            query = f"SELECT * FROM '{file_path}' LIMIT {limit}"
        return await self.execute_query(query, limit=limit)

    async def register_file_as_table(self, file_path: str, table_name: str) -> bool:
        """Create a virtual table view over a file with smart header detection."""
        if not self._conn:
            return False
        try:
            from shared.data_utils import smart_load_file
            smart_load_file(self._conn, file_path, table_name, use_llm=True)
            return True
        except Exception as exc:
            logger.warning("DuckDB register_file failed: %s", exc)
            return False

    # ── Sprint 17: Multi-Modal Fabric capabilities ─────────────────

    def capabilities(self) -> Dict[str, bool]:
        """DuckDB always supports SQL + file_query; spatial reports
        ``True`` only when the extension successfully loaded during
        connect(). The service-side dispatch in connectors/main.py
        reads this dict before routing to spatial_query()."""
        return {
            "sql": True,
            "vector": False,
            "spatial": self._spatial_loaded,
            "file_query": True,
        }

    async def spatial_query(
        self,
        query: str,
        params: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a DuckDB-spatial query (ST_* functions, GeoJSON
        ingest, R-tree spatial joins). Requires the spatial extension
        loaded — raises NotImplementedError otherwise so the caller
        gets a 501 instead of an opaque SQL parse error.

        The query string is passed verbatim. DuckDB-spatial supports
        most PostGIS function names (``ST_Distance``, ``ST_Within``,
        ``ST_Buffer``, etc.) so portable spatial queries written for
        Postgres+PostGIS usually run unchanged here."""
        if not self._spatial_loaded:
            raise NotImplementedError(
                "DuckDB spatial extension not loaded. Configure the "
                "connector with extra_params={'enable_spatial': True} "
                "or use SourceType.DUCKDB_SPATIAL."
            )
        return await self.execute_query(query, limit=10_000)
