"""BUG-198 / BUG-201: BigQuery table references and the saved-connection dataset/project mapping.

There is no local BigQuery, so the client is a recording fake; what is asserted is the SQL text and the
arguments the connector builds, which is exactly where the defects were.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("google.cloud.bigquery")

from connectors.base import ConnectorConfig, SourceType  # noqa: E402
from connectors.bigquery_connector import BigQueryConnector, build_table_ref  # noqa: E402


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def result(self):
        return iter(self._rows)


class _Client:
    def __init__(self):
        self.queries = []

    def query(self, sql):
        self.queries.append(sql)
        return _Result()

    def get_table(self, ref):
        self.queries.append(("get_table", ref))
        raise RuntimeError("no network in tests")


def _connector(database="ds", project="proj"):
    c = BigQueryConnector(ConnectorConfig(source_type=SourceType.BIGQUERY, name="bq", database=database))
    c.client, c.project_id, c._is_connected = _Client(), project, True
    return c


def test_build_table_ref_accepts_normal_names():
    assert build_table_ref("my-proj:eu", "sales_2024", "orders-v2") == "my-proj:eu.sales_2024.orders-v2"


@pytest.mark.parametrize("project,dataset,table", [
    ("proj", "ds", "t` LIMIT 1; DROP TABLE x --"),
    ("proj", "ds`; --", "t"),
    ("pr`oj", "ds", "t"),
    ("proj", "ds", "a.b"),
    ("proj", "", "t"),
    ("", "ds", "t"),
    ("proj", "ds", ""),
])
def test_build_table_ref_rejects_anything_that_could_break_out(project, dataset, table):
    with pytest.raises(ValueError):
        build_table_ref(project, dataset, table)


def test_sample_rows_never_sends_an_injected_table_name_to_bigquery():
    c = _connector()
    assert asyncio.run(c.sample_rows("t` LIMIT 1; DROP TABLE x --")) == []
    assert c.client.queries == [], "an invalid name must be refused before any query is built"


def test_sample_rows_builds_a_quoted_reference_for_a_valid_name():
    c = _connector()
    asyncio.run(c.sample_rows("orders", limit=5))
    assert c.client.queries == ["SELECT * FROM `proj.ds.orders` LIMIT 5"]


def test_profile_table_refuses_an_injected_name():
    c = _connector()
    out = asyncio.run(c.profile_table("t`; DROP TABLE x --"))
    assert all("DROP" not in str(q) for q in c.client.queries)
    assert not out or "error" in str(out).lower() or out == {}


def test_gateway_profile_route_rejects_a_bad_table_name():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api_gateway.routers.connections import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    r = TestClient(app).post("/api/v1/connectors/bigquery/profile", json={
        "connector_type": "bigquery", "connector_config": {}, "table_name": "x y; DROP TABLE z"})
    assert r.status_code == 400


def test_saved_bigquery_connection_uses_the_dataset_not_the_project_as_database():
    """BUG-201: the mapping put project_id into `database`, which BigQuery treats as the dataset."""
    from api_gateway.routers.connections import _stored_connector_config

    cfg = _stored_connector_config({"type": "bigquery", "name": "bq", "database": None}, None,
                                   {"project_id": "my-proj", "dataset": "sales", "credentials_json": {"k": "v"}})
    assert cfg.database == "sales"
    assert cfg.extra_params == {"project_id": "my-proj"}
    c = BigQueryConnector(cfg)
    c.project_id = cfg.extra_params["project_id"]
    assert build_table_ref(c.project_id, cfg.database, "orders") == "my-proj.sales.orders"
