"""
DuckDB spatial connector tests — Sprint 17 (Multi-Modal Fabric,
Pillar 2).

Covers:

* SourceType.DUCKDB_SPATIAL enum entry exists.
* ConnectorSpec for 'duckdb_spatial' is registered with spatial in
  capabilities (registration is independent of whether the extension
  can actually load — UI users need to be able to PICK it before
  the system tries to load).
* When the spatial extension loads successfully, the connector's
  runtime capabilities() reports spatial=True and spatial_query()
  works against a simple ST_Distance query.
* When the spatial extension can't load (offline / air-gapped /
  extension-server unreachable), the connector still works for SQL
  and capabilities() reports spatial=False — fail-closed semantics.
* spatial_query() on a connector that didn't load the extension
  raises NotImplementedError (BaseConnector default propagates),
  giving the caller a clean 501-style signal.
"""
from __future__ import annotations

import pytest

duckdb = pytest.importorskip(
    "duckdb",
    reason="duckdb driver not installed",
)

from connectors import (
    ConnectorConfig,
    DuckDBConnector,
    SourceType,
    available_connectors,
)

# ── Registry surface ──────────────────────────────────────────────────

def test_duckdb_spatial_source_type_enum_present():
    assert SourceType.DUCKDB_SPATIAL.value == "duckdb_spatial"


def test_duckdb_spatial_spec_registered():
    """The 'duckdb_spatial' connector ID is in the registry with
    spatial in its declared capabilities. Available state depends on
    duckdb being importable, NOT on whether the spatial extension
    actually loads — registration is a UI concern, runtime is a
    capability concern."""
    specs = {s.id: s for s in available_connectors()}
    assert "duckdb_spatial" in specs, (
        f"duckdb_spatial not registered. Got: {list(specs.keys())}"
    )
    spec = specs["duckdb_spatial"]
    assert spec.available is True
    assert "spatial" in spec.capabilities


# ── Lifecycle + capabilities ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_duckdb_spatial_loads_extension_when_available():
    """When the DuckDB spatial extension is reachable, connecting via
    SourceType.DUCKDB_SPATIAL loads it and capabilities() reports
    spatial=True. We can't guarantee the extension server is reachable
    in all CI environments, so this test allows either outcome and
    just asserts the connector is internally consistent."""
    cfg = ConnectorConfig(
        source_type=SourceType.DUCKDB_SPATIAL,
        name="test_spatial",
        extra_params={"db_path": ":memory:"},
    )
    c = DuckDBConnector(cfg)
    ok = await c.connect()
    assert ok is True  # connect itself never fails on the extension
    assert c.is_connected() is True

    caps = c.capabilities()
    if caps["spatial"]:
        # Extension loaded — round-trip a basic spatial query
        rows = await c.spatial_query(
            "SELECT ST_AsText(ST_Point(1, 2)) AS pt"
        )
        assert len(rows) == 1
        # ST_AsText format: "POINT (1 2)"
        pt_str = list(rows[0].values())[0]
        assert "POINT" in pt_str.upper()
    else:
        # Extension unavailable — spatial_query MUST raise to give the
        # caller a clean 501-style signal
        with pytest.raises(NotImplementedError, match="spatial extension"):
            await c.spatial_query("SELECT 1")

    await c.disconnect()


@pytest.mark.asyncio
async def test_duckdb_without_spatial_has_capability_false():
    """Plain SourceType.DUCKDB (no enable_spatial) reports
    spatial=False and spatial_query raises NotImplementedError —
    the existing DuckDB connector behavior is unchanged."""
    cfg = ConnectorConfig(
        source_type=SourceType.DUCKDB,
        name="test_plain_duckdb",
        extra_params={"db_path": ":memory:"},
    )
    c = DuckDBConnector(cfg)
    await c.connect()
    caps = c.capabilities()
    assert caps["spatial"] is False
    assert caps["sql"] is True
    with pytest.raises(NotImplementedError, match="spatial extension"):
        await c.spatial_query("SELECT 1")
    await c.disconnect()


@pytest.mark.asyncio
async def test_duckdb_enable_spatial_via_extra_params():
    """Setting extra_params={'enable_spatial': True} on a plain DuckDB
    connector triggers the same load path as SourceType.DUCKDB_SPATIAL.
    Lets users opt in to spatial without reconfiguring the source type."""
    cfg = ConnectorConfig(
        source_type=SourceType.DUCKDB,
        name="test_optin_spatial",
        extra_params={"db_path": ":memory:", "enable_spatial": True},
    )
    c = DuckDBConnector(cfg)
    await c.connect()
    caps = c.capabilities()
    if caps["spatial"]:
        rows = await c.spatial_query("SELECT ST_Distance(ST_Point(0,0), ST_Point(3,4)) AS d")
        assert abs(float(list(rows[0].values())[0]) - 5.0) < 1e-6
    await c.disconnect()
