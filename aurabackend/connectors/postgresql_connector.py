"""
PostgreSQL connector for AURA
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

from .base import BaseConnector, ConnectorConfig


class PostgreSQLConnector(BaseConnector):
    """Connect to and profile PostgreSQL databases"""

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.pool: Optional[asyncpg.pool.Pool] = None

    async def connect(self) -> bool:
        """Establish connection to PostgreSQL"""
        try:
            self.pool = await asyncpg.create_pool(
                user=self.config.username or "postgres",
                password=self.config.password or "",
                database=self.config.database or "postgres",
                host=self.config.host or "localhost",
                port=self.config.port or 5432,
                min_size=1,
                max_size=int(os.getenv("DB_POOL_SIZE", "10")),
            )
            self._is_connected = True
            self.metadata.connected = True
            self.metadata.last_sync = datetime.now().isoformat()
            return True
        except Exception as e:
            print(f"PostgreSQL connection failed: {e}")
            return False

    async def disconnect(self) -> bool:
        """Close PostgreSQL connection"""
        try:
            if self.pool:
                await self.pool.close()
            self._is_connected = False
            self.metadata.connected = False
            return True
        except Exception as e:
            print(f"Disconnect failed: {e}")
            return False

    async def list_tables(self) -> List[str]:
        """List all tables in the database"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    ORDER BY table_name
                    """
                )
                tables = [row["table_name"] for row in rows]
                self.metadata.table_count = len(tables)
                return tables
        except Exception as e:
            print(f"Failed to list tables: {e}")
            return []

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get schema for a PostgreSQL table"""
        if not self._is_connected or not self.pool:
            return {}

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = $1
                    ORDER BY ordinal_position
                    """,
                    table_name,
                )

                schema = {
                    "table_name": table_name,
                    "columns": [
                        {
                            "name": row["column_name"],
                            "type": row["data_type"],
                            "nullable": row["is_nullable"] == "YES",
                        }
                        for row in rows
                    ],
                }
                return schema
        except Exception as e:
            print(f"Failed to get schema: {e}")
            return {}

    async def sample_rows(
        self,
        table_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get sample rows from a PostgreSQL table"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    f"SELECT * FROM {table_name} LIMIT {limit}"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"Failed to sample rows: {e}")
            return []

    async def execute_query(self, query: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Execute SQL query against PostgreSQL"""
        if not self._is_connected or not self.pool:
            return []

        try:
            async with self.pool.acquire() as conn:
                # Append LIMIT if not present
                if "LIMIT" not in query.upper():
                    query = f"{query} LIMIT {limit}"

                rows = await conn.fetch(query)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"Query execution failed: {e}")
            return []

    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Generate comprehensive profile for a PostgreSQL table"""
        if not self._is_connected or not self.pool:
            return {}

        try:
            schema = await self.get_table_schema(table_name)
            samples = await self.sample_rows(table_name, limit=1000)

            # Count rows
            async with self.pool.acquire() as conn:
                row_count = await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")

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
                    "samples": col_values[:10] if col_values else [],
                }

                # Add numeric stats if applicable
                if col_type in ("integer", "bigint", "decimal", "numeric", "real", "double precision"):
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
            print(f"Profiling failed: {e}")
            return {}
