"""
DuckDB connector for AURA — fast local analytics
Queries CSV, Parquet, JSON, Excel files directly via SQL.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base import BaseConnector, ConnectorConfig


class DuckDBConnector(BaseConnector):
    """In-process analytical database — no server needed."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._conn: Any = None

    async def connect(self) -> bool:
        try:
            import duckdb  # type: ignore[import-untyped]
        except ImportError:
            print("DuckDBConnector: duckdb is not installed")
            return False
        try:
            path = (
                self.config.connection_string
                or (self.config.extra_params or {}).get("db_path", ":memory:")
            )
            self._conn = duckdb.connect(str(path))
            self._is_connected = True
            self.metadata.connected = True
            return True
        except Exception as exc:
            print(f"DuckDB connection failed: {exc}")
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
            print(f"DuckDB list_tables failed: {exc}")
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
            print(f"DuckDB get_table_schema failed: {exc}")
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
            print(f"DuckDB query failed: {exc}")
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
            print(f"DuckDB profile failed: {exc}")
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
            print(f"DuckDB register_file failed: {exc}")
            return False
