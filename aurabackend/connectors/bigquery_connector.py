"""
BigQuery connector for AURA
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from google.oauth2 import service_account

from .base import BaseConnector, ConnectorConfig

logger = logging.getLogger("aura.connectors.bigquery")


class BigQueryConnector(BaseConnector):
    """Connect to and profile Google BigQuery"""

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.client: Optional[bigquery.Client] = None
        self.project_id: Optional[str] = None

    async def connect(self) -> bool:
        """Establish connection to BigQuery"""
        try:
            if self.config.credentials_json:
                # Load credentials from JSON
                credentials = service_account.Credentials.from_service_account_info(
                    self.config.credentials_json
                )
                self.client = bigquery.Client(credentials=credentials)
                self.project_id = credentials.project_id
            else:
                # Use default credentials from environment
                self.client = bigquery.Client(project=self.config.database)
                self.project_id = self.config.database

            self._is_connected = True
            self.metadata.connected = True
            self.metadata.last_sync = datetime.now().isoformat()
            return True
        except Exception as e:
            logger.warning("BigQuery connection failed: %s", e)
            return False

    async def disconnect(self) -> bool:
        """Close BigQuery connection"""
        try:
            if self.client:
                self.client.close()
            self._is_connected = False
            self.metadata.connected = False
            return True
        except Exception as e:
            logger.warning("BigQuery disconnect failed: %s", e)
            return False

    async def list_tables(self) -> List[str]:
        """List all tables in BigQuery dataset"""
        if not self._is_connected or not self.client:
            return []

        try:
            dataset_ref = self.client.dataset(self.config.database or "")
            tables = list(self.client.list_tables(dataset_ref))
            table_list = [table.table_id for table in tables]
            self.metadata.table_count = len(table_list)
            return table_list
        except Exception as e:
            logger.warning("BigQuery list_tables failed: %s", e)
            return []

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get schema for a BigQuery table"""
        if not self._is_connected or not self.client:
            return {}

        try:
            dataset_id = self.config.database or ""
            table_id = f"{self.project_id}.{dataset_id}.{table_name}"
            table = self.client.get_table(table_id)

            schema = {
                "table_name": table_name,
                "columns": [
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "nullable": field.mode != "REQUIRED",
                    }
                    for field in table.schema
                ],
            }
            return schema
        except Exception as e:
            logger.warning("BigQuery get_schema failed: %s", e)
            return {}

    async def sample_rows(
        self,
        table_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get sample rows from a BigQuery table"""
        if not self._is_connected or not self.client:
            return []

        try:
            dataset_id = self.config.database or ""
            table_id = f"{self.project_id}.{dataset_id}.{table_name}"

            query = f"SELECT * FROM `{table_id}` LIMIT {limit}"
            results = self.client.query(query).result()

            rows = []
            for row in results:
                rows.append(dict(row.items()))
            return rows
        except Exception as e:
            logger.warning("BigQuery sample_rows failed: %s", e)
            return []

    async def execute_query(self, query: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Execute SQL query against BigQuery"""
        if not self._is_connected or not self.client:
            return []

        try:
            # Append LIMIT if not present
            if "LIMIT" not in query.upper():
                query = f"{query} LIMIT {limit}"

            results = self.client.query(query).result()

            rows = []
            for row in results:
                rows.append(dict(row.items()))
            return rows
        except Exception as e:
            logger.warning("BigQuery query failed: %s", e)
            return []

    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Generate comprehensive profile for a BigQuery table"""
        if not self._is_connected or not self.client:
            return {}

        try:
            schema = await self.get_table_schema(table_name)
            samples = await self.sample_rows(table_name, limit=1000)

            # Get row count
            dataset_id = self.config.database or ""
            table_id = f"{self.project_id}.{dataset_id}.{table_name}"

            count_query = f"SELECT COUNT(*) as cnt FROM `{table_id}`"
            count_result = self.client.query(count_query).result()
            row_count = next(count_result)[0]

            # Profile each column
            columns_profile = {}
            for col in schema.get("columns", []):
                col_name = col["name"]
                col_type = col["type"]

                # Extract values
                col_values = [s.get(col_name) for s in samples if s.get(col_name) is not None]

                columns_profile[col_name] = {
                    "data_type": col_type,
                    "non_null": len(col_values),
                    "nulls": len(samples) - len(col_values),
                    "distinct": len(set(str(v) for v in col_values)),
                    "samples": [str(v) for v in col_values[:10]] if col_values else [],
                }

                # Add numeric stats if applicable
                if col_type in ("INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
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
            logger.warning("BigQuery profiling failed: %s", e)
            return {}
