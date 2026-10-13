"""BUG-440: the BigQuery row cap must not be skipped because the text mentions "limit"."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("google.cloud.bigquery")

from connectors.base import ConnectorConfig, SourceType  # noqa: E402
from connectors.bigquery_connector import BigQueryConnector  # noqa: E402


class _Client:
    def __init__(self):
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)

        class _Job:
            def result(self_inner):
                return []

        return _Job()


def _sent(query: str) -> str:
    c = BigQueryConnector(ConnectorConfig(source_type=SourceType.BIGQUERY, name="bq", database="sales"))
    c.client = _Client()
    c.project_id = "proj"
    c._is_connected = True
    asyncio.run(c.execute_query(query, limit=50))
    return c.client.sql[-1]


@pytest.mark.parametrize("query", [
    "SELECT credit_limit FROM accounts",
    "SELECT * FROM accounts WHERE note = 'no limit'",
    "SELECT * FROM (SELECT * FROM accounts LIMIT 100000) AS a",
    "SELECT * FROM accounts;",
])
def test_the_cap_is_appended_when_limit_only_appears_elsewhere(query):
    assert _sent(query).rstrip().endswith("LIMIT 50")


def test_a_query_with_its_own_trailing_limit_is_left_alone():
    assert _sent("SELECT * FROM accounts LIMIT 5") == "SELECT * FROM accounts LIMIT 5"
