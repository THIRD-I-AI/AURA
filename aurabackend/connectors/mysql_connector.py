"""
MySQL connector for AURA
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiomysql

from .base import BaseConnector, ConnectorConfig

logger = logging.getLogger("aura.connectors.mysql")


class MySQLConnector(BaseConnector):
    """Connect to and profile MySQL databases"""

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.pool: Optional[aiomysql.Pool] = None

    async def connect(self) -> bool:
        """Establish connection to MySQL"""
        try:
            self.pool = await aiomysql.create_pool(
                host=self.config.host or "localhost",
                port=self.config.port or 3306,
                user=self.config.username or "root",
                password=self.config.password or "",
                db=self.config.database or "mysql",
                minsize=1,
                maxsize=10,
            )
            self._is_connected = True
            self.metadata.connected = True
            self.metadata.last_sync = datetime.now().isoformat()
            return True
        except Exception as e:
            logger.warning("MySQL connection failed: %s", e)
            return False

    async def disconnect(self) -> bool:
        """Close MySQL connection"""
        try:
            if self.pool:
                self.pool.close()
                await self.pool.wait_closed()
            self._is_connected = False
            self.metadata.connected = False
            return True
        except Exception as e:
            logger.warning("MySQL disconnect failed: %s", e)
            return False

    async def list_tables(self) -> List[str]:
        """List all tables in the database"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE()"
                    )
                    rows = await cur.fetchall()
                    tables = [row[0] for row in rows]
                    self.metadata.table_count = len(tables)
                    return tables
        except Exception as e:
            logger.warning("MySQL list_tables failed: %s", e)
            return []

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get schema for a MySQL table"""
        if not self._is_connected or not self.pool:
            return {}

        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"DESC {table_name}"
                    )
                    rows = await cur.fetchall()

                    schema = {
                        "table_name": table_name,
                        "columns": [
                            {
                                "name": row[0],
                                "type": row[1],
                                "nullable": row[2] == "YES",
                            }
                            for row in rows
                        ],
                    }
                    return schema
        except Exception as e:
            logger.warning("MySQL get_schema failed: %s", e)
            return {}

    async def sample_rows(
        self,
        table_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get sample rows from a MySQL table"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
                    rows = await cur.fetchall()
                    return rows or []
        except Exception as e:
            logger.warning("MySQL sample_rows failed: %s", e)
            return []

    async def execute_query(self, query: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Execute SQL query against MySQL"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    # Append LIMIT if not present
                    if "LIMIT" not in query.upper():
                        query = f"{query} LIMIT {limit}"

                    await cur.execute(query)
                    rows = await cur.fetchall()
                    return rows or []
        except Exception as e:
            logger.warning("MySQL query failed: %s", e)
            return []

    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Generate comprehensive profile for a MySQL table"""
        if not self._is_connected or not self.pool:
            return {}

        try:
            schema = await self.get_table_schema(table_name)
            samples = await self.sample_rows(table_name, limit=1000)

            # Count rows
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                    row_count = (await cur.fetchone())[0]

            # Profile each column
            columns_profile = {}
            for col in schema.get("columns", []):
                col_name = col["name"]
                col_type = col["type"]

                # Extract numeric values
                col_values = [s.get(col_name) for s in samples if s.get(col_name) is not None]

                columns_profile[col_name] = {
                    "data_type": col_type,
                    "non_null": len(col_values),
                    "nulls": len(samples) - len(col_values),
                    "distinct": len(set(str(v) for v in col_values)),
                    "samples": [str(v) for v in col_values[:10]] if col_values else [],
                }

                # Add numeric stats if applicable
                if any(t in col_type.lower() for t in ("int", "decimal", "float", "double")):
                    try:
                        numeric_values = [float(v) for v in col_values if v is not None]
                        if numeric_values:
                            columns_profile[col_name]["min"] = min(numeric_values)
                            columns_profile[col_name]["max"] = max(numeric_values)
                            columns_profile[col_name]["mean"] = sum(numeric_values) / len(numeric_values)
                    except (ValueError, TypeError):
                        pass

            return {
                "table_name": table_name,
                "rows": row_count,
                "columns": len(schema.get("columns", [])),
                "columns_profile": columns_profile,
            }
        except Exception as e:
            logger.warning("MySQL profiling failed: %s", e)
            return {}
