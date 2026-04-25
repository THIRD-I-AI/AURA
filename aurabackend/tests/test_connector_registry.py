"""
Connector Registry Tests
========================
Covers the ``connectors.registry`` module — both the in-process register/
get/available API and the entry-point discovery hook used by third-party
plugins.
"""

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
