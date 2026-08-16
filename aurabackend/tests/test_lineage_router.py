"""GET /lineage must actually execute — and stay tenant-scoped.

WHY THIS EXISTS: this endpoint answered 500 on every call in production while
the whole suite stayed green. The router imported ``_dashboards_store`` and
``_dashboards_lock`` from the dashboards router; that in-memory store had been
replaced by ``persistence.DashboardRow``, so the import raised ImportError.

Two things hid it. The import is *function-local*, so importing the module —
which is all a route-table or OpenAPI test does — never touches it. And the
existing lineage tests cover ``persistence.list_lineage_edges`` and
``upsert_lineage_edges``, which were never broken: the dependency was tested,
its caller was not.

So these tests call the route function itself. Anything less would have passed
against the broken code.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence
from api_gateway.routers.lineage import get_lineage


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Rebind the async engine per test.

    pytest-asyncio gives each test its own event loop while the module-level
    engine stays bound to the first one, so the second async test in a session
    deadlocks with no output. Mirrors tests/test_dashboards_persistence.py;
    deliberately does not await close_database(), which hangs against an
    already-closed loop.
    """
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


def _request(tenant: str) -> Request:
    """A minimal authenticated request.

    current_workspace_id() reads the tenant from request.state.user (set by
    JWTAuthMiddleware in production) and the folder from a header; with no
    header the workspace IS the tenant.
    """
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/lineage",
        "headers": [],
        "query_string": b"",
    })
    request.state.user = {"org_id": tenant}
    return request


def _dashboard(ws: str, name: str = "Revenue overview") -> dict:
    now = datetime.now(timezone.utc)
    return {
        # Unique per run: these rows are durable and a fixed id would collide
        # with a previous execution on the primary key.
        "id": f"dash_{uuid.uuid4().hex[:12]}",
        "workspace_id": ws,
        "name": name,
        "description": "revenue by region",
        "tiles": [{
            "id": "tile_0",
            "saved_query_id": "sq_revenue",
            "title": "Revenue by region",
            "chart_type": "bar",
        }],
        "created_at": now.isoformat(),
        "created_ts": now.timestamp(),
        "updated_at": now.isoformat(),
    }


@pytest.mark.asyncio
async def test_lineage_route_executes():
    """THE regression: the body must run. It raised ImportError in prod."""
    result = await get_lineage(_request(f"tenant_{uuid.uuid4().hex[:8]}"))

    assert result["success"] is True
    # Empty workspace: a real graph shape, not an error payload.
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["summary"] == {"tables": 0, "queries": 0, "dashboards": 0, "edges": 0}


@pytest.mark.asyncio
async def test_lineage_includes_dashboards_from_persistence():
    """Dashboards must reach the graph through persistence, not a module global.

    Asserting the dashboard NODE is what distinguishes a working read from a
    route that merely returns 200 with an empty graph — which is exactly what
    a lenient fix (swallowing the ImportError) would have produced.
    """
    tenant = f"tenant_{uuid.uuid4().hex[:8]}"
    record = await persistence.insert_dashboard(_dashboard(tenant))

    result = await get_lineage(_request(tenant))

    dash_nodes = [n for n in result["nodes"] if n["type"] == "dashboard"]
    assert [n["label"] for n in dash_nodes] == ["Revenue overview"]
    assert dash_nodes[0]["id"] == f"d:{record['id']}"
    assert dash_nodes[0]["metadata"]["tile_count"] == 1
    assert result["summary"]["dashboards"] == 1

    # The tile's saved query becomes the edge source, so the dashboard is
    # reachable from the query layer rather than floating unconnected.
    assert {"id": f"q:sq_revenue->d:{record['id']}",
            "source": "q:sq_revenue",
            "target": f"d:{record['id']}"} in result["edges"]


@pytest.mark.asyncio
async def test_lineage_does_not_leak_dashboards_across_tenants():
    """The old code filtered an in-process list by workspace_id in Python.
    The replacement pushes that boundary into the query — verify it held."""
    tenant_a = f"tenant_{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant_{uuid.uuid4().hex[:8]}"
    await persistence.insert_dashboard(_dashboard(tenant_a, name="A private"))

    result_b = await get_lineage(_request(tenant_b))

    labels = [n["label"] for n in result_b["nodes"] if n["type"] == "dashboard"]
    assert "A private" not in labels
    assert result_b["summary"]["dashboards"] == 0
