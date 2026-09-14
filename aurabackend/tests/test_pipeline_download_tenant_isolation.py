"""
BUG-051 -- GET /pipeline/download/{filename} had no tenant/ownership check
at all, and pipeline outputs all landed in one shared, non-namespaced
directory under a caller-controlled filename. Any authenticated caller
who guessed/collided on another tenant's output filename could download
their data.

Fixed: pipeline/engine.py's _write_file_sink now writes under a
per-tenant subdirectory (tenant_slug()), and this router endpoint only
ever resolves a download inside the REQUESTING caller's own
subdirectory -- it can never reach another tenant's file regardless of
filename.
"""
from __future__ import annotations

import os
import sys

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.pipelines import pipeline_download  # noqa: E402


def _request(tenant: str) -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/pipeline/download/export.csv",
        "headers": [],
        "query_string": b"",
    })
    request.state.user = {"org_id": tenant}
    return request


@pytest.fixture()
def _output_root(tmp_path, monkeypatch):
    # pipeline_download computes output_dir from its own module's __file__
    # location (three dirs up + data/processed), not an env var -- patch
    # os.path.dirname's target indirectly isn't practical, so instead
    # monkeypatch the module-level Path import chain by writing directly
    # under the REAL computed dir but inside a uniquely-named tenant slug,
    # then clean up. Simpler: write the fixture files where the function
    # actually looks, using the real path helper it uses internally.
    from pathlib import Path

    import api_gateway.routers.pipelines as pipelines_module
    from shared.storage.base import tenant_slug
    real_output_dir = (
        Path(os.path.dirname(os.path.dirname(os.path.dirname(pipelines_module.__file__))))
        / "data" / "processed"
    )

    def write_for(tenant: str, filename: str, content: bytes) -> Path:
        d = real_output_dir / tenant_slug(tenant)
        d.mkdir(parents=True, exist_ok=True)
        p = d / filename
        p.write_bytes(content)
        return p

    yield write_for

    # Cleanup only the tenants this test created.
    import shutil
    for tenant in ("bug051-tenant-a", "bug051-tenant-b"):
        shutil.rmtree(real_output_dir / tenant_slug(tenant), ignore_errors=True)


@pytest.mark.asyncio
async def test_tenant_can_download_its_own_output(_output_root):
    _output_root("bug051-tenant-a", "export.csv", b"id,name\n1,Alice\n")
    resp = await pipeline_download("export.csv", _request("bug051-tenant-a"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_cannot_download_another_tenants_output(_output_root):
    """The core BUG-051 regression: tenant A writes an output named
    export.csv; tenant B (or any other caller) requesting the SAME
    filename must get 404, not tenant A's file."""
    _output_root("bug051-tenant-a", "export.csv", b"id,name\n1,Alice\nSECRET\n")

    with pytest.raises(Exception) as exc_info:
        await pipeline_download("export.csv", _request("bug051-tenant-b"))
    assert getattr(exc_info.value, "status_code", None) == 404
