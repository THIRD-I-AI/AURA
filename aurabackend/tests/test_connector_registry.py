"""
Connector Registry Tests
========================
Covers the ``connectors.registry`` module — both the in-process register/
get/available API and the entry-point discovery hook used by third-party
plugins.
"""

import json
import os
import sys
from typing import List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import registry as registry_module
from connectors.registry import (
    ConnectorField,
    ConnectorSpec,
    available_connectors,
    get_connector,
    register_connector,
    unregister_connector,
)

# ── Spec serialization ────────────────────────────────────────────────────────

def test_connector_spec_to_dict_includes_legacy_config_required():
    spec = ConnectorSpec(
        id="pluginx",
        name="Plugin X",
        description="example",
        kind="relational",
        fields=[
            ConnectorField("host", "Host", "string", required=True),
            ConnectorField("port", "Port", "number", required=True),
            ConnectorField("ssl", "SSL", "boolean", required=False),
        ],
    )
    d = spec.to_dict()
    assert d["id"] == "pluginx"
    assert d["available"] is True  # default
    assert d["config_required"] == ["host", "port"]
    # raw fields list also present so the UI can render the form
    assert {f["key"] for f in d["fields"]} == {"host", "port", "ssl"}


def test_field_optional_serialization_carries_help_and_default():
    f = ConnectorField(
        "credentials_json", "Service Account JSON", "textarea",
        required=True, help="Paste the service account key.",
    )
    assert f.required is True
    assert f.help.startswith("Paste")
    assert f.default is None


# ── Register / get / unregister ───────────────────────────────────────────────

@pytest.fixture
def temp_spec():
    spec = ConnectorSpec(
        id="ephemeral",
        name="Ephemeral",
        description="test-only",
        kind="embedded",
    )
    yield spec
    unregister_connector(spec.id)


def test_register_then_get_round_trips(temp_spec):
    register_connector(temp_spec)
    got = get_connector("ephemeral")
    assert got is not None
    assert got.name == "Ephemeral"


def test_register_is_idempotent_and_replaces(temp_spec):
    register_connector(temp_spec)
    replacement = ConnectorSpec(
        id="ephemeral", name="Ephemeral v2",
        description="updated", kind="embedded",
    )
    register_connector(replacement)
    got = get_connector("ephemeral")
    assert got is not None
    assert got.name == "Ephemeral v2"


def test_unregister_removes_spec(temp_spec):
    register_connector(temp_spec)
    unregister_connector("ephemeral")
    assert get_connector("ephemeral") is None


def test_available_connectors_is_sorted_alphabetically():
    names = [s.name for s in available_connectors()]
    assert names == sorted(names, key=str.lower)


def test_available_connectors_filter_unavailable(temp_spec):
    unavail = ConnectorSpec(
        id="missing-driver", name="Missing Driver",
        description="d", kind="relational",
        available=False, unavailable_reason="driver not installed",
    )
    try:
        register_connector(unavail)
        all_specs = available_connectors(include_unavailable=True)
        only_avail = available_connectors(include_unavailable=False)
        assert any(s.id == "missing-driver" for s in all_specs)
        assert not any(s.id == "missing-driver" for s in only_avail)
    finally:
        unregister_connector("missing-driver")


# ── Built-in seeding ──────────────────────────────────────────────────────────

def test_builtin_connectors_are_seeded():
    ids = {s.id for s in available_connectors()}
    assert {"postgresql", "mysql", "bigquery", "duckdb"}.issubset(ids)


def test_postgresql_spec_advertises_vector_capability():
    pg = get_connector("postgresql")
    assert pg is not None
    assert "vector" in pg.capabilities
    assert "spatial" in pg.capabilities


# ── Entry-point discovery ─────────────────────────────────────────────────────

class _FakeEntryPoint:
    def __init__(self, name, factory):
        self.name = name
        self._factory = factory

    def load(self):
        return self._factory


class _FakeEntryPoints:
    def __init__(self, by_group):
        self._by_group = by_group

    def select(self, group):
        return self._by_group.get(group, [])


def _make_fake_spec():
    return ConnectorSpec(
        id="fake-snowflake", name="Fake Snowflake",
        description="loaded via entry point",
        kind="warehouse", capabilities=["sql"],
    )


def _make_fake_specs() -> List[ConnectorSpec]:
    return [
        ConnectorSpec(id="fake-a", name="Fake A", description="", kind="embedded"),
        ConnectorSpec(id="fake-b", name="Fake B", description="", kind="embedded"),
    ]


def _broken_factory():
    raise RuntimeError("plugin exploded on load")


@pytest.fixture(autouse=True)
def _cleanup_fake_specs():
    yield
    for sid in ("fake-snowflake", "fake-a", "fake-b", "fake-bogus"):
        unregister_connector(sid)


def test_load_entry_points_registers_single_spec(monkeypatch):
    fake_eps = _FakeEntryPoints({
        registry_module.ENTRY_POINT_GROUP: [
            _FakeEntryPoint("fake-snowflake", _make_fake_spec),
        ],
    })
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda: fake_eps,
    )
    registry_module._load_entry_point_connectors()
    got = get_connector("fake-snowflake")
    assert got is not None
    assert got.kind == "warehouse"


def test_load_entry_points_accepts_iterable(monkeypatch):
    fake_eps = _FakeEntryPoints({
        registry_module.ENTRY_POINT_GROUP: [
            _FakeEntryPoint("multi", _make_fake_specs),
        ],
    })
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda: fake_eps,
    )
    registry_module._load_entry_point_connectors()
    assert get_connector("fake-a") is not None
    assert get_connector("fake-b") is not None


def test_load_entry_points_swallows_broken_plugin(monkeypatch):
    fake_eps = _FakeEntryPoints({
        registry_module.ENTRY_POINT_GROUP: [
            _FakeEntryPoint("bad", _broken_factory),
            _FakeEntryPoint("good", _make_fake_spec),
        ],
    })
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda: fake_eps,
    )
    # Must NOT raise — broken plugin is logged + skipped, good one still loads.
    registry_module._load_entry_point_connectors()
    assert get_connector("fake-snowflake") is not None


def test_load_entry_points_ignores_non_spec_return(monkeypatch):
    fake_eps = _FakeEntryPoints({
        registry_module.ENTRY_POINT_GROUP: [
            _FakeEntryPoint("bogus", lambda: {"id": "fake-bogus", "name": "Dict"}),
        ],
    })
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda: fake_eps,
    )
    registry_module._load_entry_point_connectors()
    # Dict was returned instead of ConnectorSpec — must not register.
    assert get_connector("fake-bogus") is None


# ── Gateway integration: create_connection guards against unknown types ───

@pytest.fixture
def connections_client():
    """TestClient mounting only the connections router so we don't pull in
    the full gateway lifespan (no DB / no scheduler / no LLM init)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api_gateway.routers.connections import router as connections_router

    app = FastAPI()
    app.include_router(connections_router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client


def test_create_connection_rejects_unknown_type(connections_client):
    resp = connections_client.post("/api/v1/connections", json={
        "name": "test-bad", "type": "nonexistent-db-flavor",
    })
    assert resp.status_code == 400
    body = resp.json()
    detail = body["detail"]
    assert "Unknown connector type" in detail["error"]
    assert "duckdb" in detail["valid_types"]


def test_create_connection_rejects_unavailable_driver(connections_client, monkeypatch):
    spec = ConnectorSpec(
        id="needs-driver", name="Needs Driver",
        description="", kind="relational",
        available=False, unavailable_reason="foodriver not installed",
    )
    register_connector(spec)
    try:
        resp = connections_client.post("/api/v1/connections", json={
            "name": "x", "type": "needs-driver",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "unavailable" in detail["error"]
        assert detail["reason"] == "foodriver not installed"
    finally:
        unregister_connector("needs-driver")


# BUG-165: POST /connections accepted an `extra` dict (and connectors like
# BigQuery whose required settings live outside host/port/database/username/
# password/ssl) and returned 200 while storing none of it. Silent data loss:
# the caller believes a working connection exists.

def _connection_count(client):
    return client.get("/api/v1/connections").json()["count"]


def test_create_connection_stores_extra_and_never_returns_it(connections_client):
    """BUG-170: connector-specific settings are persisted (encrypted) instead of rejected."""
    resp = connections_client.post("/api/v1/connections", json={
        "name": "vec", "type": "faiss", "database": "idx.faiss",
        "extra": {"dimension": 384, "index_type": "zz-marker-hnsw"},
    })
    assert resp.status_code == 200, resp.text
    conn = resp.json()["connection"]
    # A distinctive string, not the number 384: the response carries a microsecond
    # timestamp and "384" matches it about 1 run in 1000 (BUG-181).
    assert "extra" not in conn and "config_encrypted" not in conn and "zz-marker-hnsw" not in json.dumps(conn)
    connections_client.delete(f"/api/v1/connections/{conn['id']}")


def test_create_connection_rejects_unknown_extra_keys(connections_client):
    before = _connection_count(connections_client)
    resp = connections_client.post("/api/v1/connections", json={
        "name": "vec", "type": "faiss", "database": "idx.faiss", "extra": {"not_a_setting": 1},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["unsupported_fields"] == ["not_a_setting"]
    assert _connection_count(connections_client) == before, "a rejected request must not create a connection"


def test_create_connection_requires_the_settings_a_connector_cannot_work_without(connections_client):
    before = _connection_count(connections_client)
    resp = connections_client.post("/api/v1/connections", json={
        "name": "warehouse", "type": "bigquery", "database": "my_dataset",
    })
    assert resp.status_code == 400
    assert set(resp.json()["detail"]["missing_fields"]) == {"project_id", "credentials_json"}
    assert _connection_count(connections_client) == before


def test_create_connection_rejects_credentials_that_are_not_a_json_object(connections_client):
    resp = connections_client.post("/api/v1/connections", json={
        "name": "warehouse", "type": "bigquery",
        "extra": {"project_id": "p", "credentials_json": "not json"},
    })
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]["error"]
    assert "not json" not in resp.text


def test_create_connection_still_accepts_empty_extra_and_stays_lenient_about_required_fields(connections_client):
    """The guard must not become a validator: this endpoint has never enforced
    the registry's `required` flags (a postgresql connection without a password
    is accepted), and an empty `extra` is not data loss."""
    resp = connections_client.post("/api/v1/connections", json={
        "name": "pg", "type": "postgresql", "host": "localhost", "port": 5432,
        "database": "d", "username": "u", "extra": {},
    })
    assert resp.status_code == 200
    connections_client.delete(f"/api/v1/connections/{resp.json()['connection']['id']}")


def test_create_connection_accepts_known_type(connections_client):
    resp = connections_client.post("/api/v1/connections", json={
        "name": "my-duck", "type": "duckdb",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["connection"]["type"] == "duckdb"
    # Cleanup the in-memory store entry we just created.
    conn_id = body["connection"]["id"]
    connections_client.delete(f"/api/v1/connections/{conn_id}")


# BUG-170: at-rest encryption and the way stored settings reach the connector.

def test_deeply_nested_credentials_get_a_400_not_a_500(connections_client):
    """BUG-174: json.loads on ~5k nested brackets (well under the size cap) raises RecursionError, which is not a ValueError."""
    resp = connections_client.post("/api/v1/connections", json={
        "name": "warehouse", "type": "bigquery",
        "extra": {"project_id": "p", "credentials_json": "[" * 5_000},
    })
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]["error"]


def test_oversized_credentials_are_refused_before_parsing(connections_client):
    resp = connections_client.post("/api/v1/connections", json={
        "name": "warehouse", "type": "bigquery",
        "extra": {"project_id": "p", "credentials_json": "x" * 70_000},
    })
    assert resp.status_code == 413


def test_extra_is_encrypted_at_rest_and_round_trips():
    import asyncio
    import uuid

    from sqlalchemy import select

    from api_gateway import persistence

    secret = {"credentials_json": {"type": "service_account", "private_key": "TOP-SECRET-KEY"}, "project_id": "p1"}
    cid = str(uuid.uuid4())
    record = {
        "id": cid, "workspace_id": "ws-170", "name": "bq", "type": "bigquery",
        "created_at": "2026-09-25T00:00:00", "created_ts": 1.0, "updated_at": "2026-09-25T00:00:00",
    }

    async def go():
        await persistence.insert_connection(record, None, secret)
        async with persistence.session_scope() as s:
            raw = (await s.execute(
                select(persistence.ConnectionRow.config_encrypted).where(persistence.ConnectionRow.id == cid)
            )).scalar_one()
        return raw, await persistence.get_connection_extra(cid, "ws-170"), await persistence.get_connection_extra(cid, "other-ws")

    raw, mine, theirs = asyncio.run(go())
    assert raw and "TOP-SECRET-KEY" not in raw and "service_account" not in raw
    assert mine == secret
    assert theirs == {}, "another workspace must not read this connection's settings"


def test_stored_connector_config_maps_bigquery_settings_onto_the_connector():
    from api_gateway.routers.connections import _stored_connector_config

    conn = {"type": "bigquery", "name": "bq", "database": None}
    cfg = _stored_connector_config(conn, None, {"project_id": "proj", "dataset": "d", "credentials_json": {"k": "v"}})
    assert cfg.database == "proj"
    assert cfg.credentials_json == {"k": "v"}
    assert cfg.extra_params == {"dataset": "d"}
