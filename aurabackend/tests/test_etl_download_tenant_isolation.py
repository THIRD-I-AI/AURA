"""
BUG-052 -- /etl/execute wrote output to one shared, non-tenant-scoped
local directory (unlike the source-read side, which was already
tenant-sandboxed per BUG-035), and GET /etl/download/{filename} had no
ownership check -- only a path-traversal guard. Two tenants colliding on
the same destination_filename let either one download the other's
transformed data.

Fixed: etl_execute now writes under a per-tenant subdirectory
(tenant_slug(), same convention as the source side and pipelines.py's
BUG-051 fix), and etl_download only ever resolves inside the REQUESTING
caller's own subdirectory.

Mounts a dedicated app with JWTAuthMiddleware explicitly armed (mirrors
test_collab.py's `jwt_client` pattern) -- api_gateway.main.app decides
whether to install that middleware once, at import time, from
settings.jwt_enabled, so monkeypatching the setting after import has no
effect on the shared app singleton.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api_gateway.routers.etl import router as etl_router  # noqa: E402
from shared.auth import create_access_token  # noqa: E402
from shared.middleware import JWTAuthMiddleware  # noqa: E402


def _auth(org_id: str) -> dict:
    token = create_access_token({"sub": f"user-{org_id}", "org_id": org_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write("bug052-tenant-a", "sales.csv", b"region,revenue\nN,100\nS,200\n")
    get_storage_backend().write("bug052-tenant-b", "sales.csv", b"region,revenue\nSECRET,999\n")

    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)
    app.include_router(etl_router, prefix="/api/v1")
    with TestClient(app) as c:
        yield c


def _run_etl(client, org_id: str, dest_name: str = "export"):
    return client.post(
        "/api/v1/etl/execute",
        json={
            "source_file": "sales.csv",
            "transforms": [],
            "preview_only": False,
            "destination_filename": dest_name,
            "destination_format": "csv",
        },
        headers=_auth(org_id),
    )


def test_two_tenants_colliding_on_the_same_filename_get_separate_outputs(client):
    r_a = _run_etl(client, "bug052-tenant-a")
    assert r_a.status_code == 200, r_a.text
    assert r_a.json()["status"] == "success", r_a.json()

    r_b = _run_etl(client, "bug052-tenant-b")
    assert r_b.status_code == 200, r_b.text
    assert r_b.json()["status"] == "success", r_b.json()

    # Tenant A downloads its own output fine.
    dl_a = client.get("/api/v1/etl/download/export.csv", headers=_auth("bug052-tenant-a"))
    assert dl_a.status_code == 200
    assert b"100" in dl_a.content

    # The core BUG-052 regression: tenant B requesting the SAME filename
    # must get its OWN data, never tenant A's.
    dl_b = client.get("/api/v1/etl/download/export.csv", headers=_auth("bug052-tenant-b"))
    assert dl_b.status_code == 200
    assert b"SECRET" in dl_b.content
    assert b"N,100" not in dl_b.content


def test_tenant_cannot_download_by_guessing_before_running_their_own_etl(client):
    """A caller who never ran a matching ETL job at all must get 404, not
    another tenant's file, purely by guessing a common filename."""
    r_a = _run_etl(client, "bug052-tenant-a", dest_name="guessable")
    assert r_a.status_code == 200 and r_a.json()["status"] == "success", r_a.json()

    dl = client.get("/api/v1/etl/download/guessable.csv", headers=_auth("bug052-tenant-never-ran-etl"))
    assert dl.status_code == 404
