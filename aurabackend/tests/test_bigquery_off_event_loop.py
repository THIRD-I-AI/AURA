"""BUG-339: the BigQuery connector called the synchronous google-cloud-bigquery client
(HTTP round trips, blocking .result()) directly inside async methods, holding the
gateway's only event loop for the whole schema load or query.

No BigQuery is reachable from the test lane; the client is replaced by a stand-in that
records whether each call ran on the event loop's thread."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("google.cloud.bigquery")

from connectors.base import ConnectorConfig, SourceType  # noqa: E402
from connectors.bigquery_connector import BigQueryConnector  # noqa: E402


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class _Row:
    def __init__(self, data):
        self._data = data

    def items(self):
        return self._data.items()


class RecordingClient:
    def __init__(self):
        self.calls = {}

    def dataset(self, name):
        return name

    def list_tables(self, dataset_ref):
        self.calls["list_tables"] = _on_event_loop()
        return [SimpleNamespace(table_id="orders")]

    def get_table(self, table_id):
        self.calls["get_table"] = _on_event_loop()
        return SimpleNamespace(schema=[SimpleNamespace(name="amount", field_type="INTEGER", mode="NULLABLE")])

    def query(self, sql):
        on_loop = _on_event_loop()
        calls = self.calls

        class _Job:
            def result(self_inner):
                calls.setdefault("query", []).append(on_loop or _on_event_loop())
                return [_Row({"cnt": 3})] if "COUNT" in sql else [_Row({"amount": 1}), _Row({"amount": 2})]

        return _Job()


def _connector() -> BigQueryConnector:
    c = BigQueryConnector(ConnectorConfig(source_type=SourceType.BIGQUERY, name="bq", database="sales"))
    c.client = RecordingClient()
    c.project_id = "proj"
    c._is_connected = True
    return c


def test_every_bigquery_call_runs_off_the_event_loop():
    c = _connector()

    async def scenario():
        await c.list_tables()
        await c.get_table_schema("orders")
        rows = await c.execute_query("SELECT amount FROM orders")
        profile = await c.profile_table("orders")
        return rows, profile

    rows, profile = asyncio.run(scenario())

    assert rows == [{"amount": 1}, {"amount": 2}]
    assert profile  # the COUNT went through too
    calls = c.client.calls
    assert calls["list_tables"] is False and calls["get_table"] is False
    assert calls["query"] and not any(calls["query"]), calls
