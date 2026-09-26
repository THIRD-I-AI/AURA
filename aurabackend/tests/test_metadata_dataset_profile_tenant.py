"""BUG-018 (remaining gap): the metadata service's dataset-profile routes were tenant-blind -- the repository
already scoped by workspace_id, but the routes never passed it, so any caller could read or overwrite any
profile (including its sample data)."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from metadata_store.db import Base
from metadata_store.main import metadata_app
from metadata_store.repository import MetadataRepository, get_repository


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _repo():
        async with maker() as session:
            yield MetadataRepository(session)

    metadata_app.dependency_overrides[get_repository] = _repo
    transport = httpx.ASGITransport(app=metadata_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    metadata_app.dependency_overrides.clear()
    await engine.dispose()


def _h(ws):
    return {"X-Workspace-Id": ws}


@pytest.mark.asyncio
async def test_a_profile_is_invisible_to_another_workspace(client):
    body = {"dataset_name": "sales", "profile": {"sample": [{"secret": "A-ONLY"}]}, "rows_count": 1, "columns_count": 1}
    assert (await client.post("/dataset-profiles/sales.csv", json=body, headers=_h("ws-a"))).status_code == 200

    mine = await client.get("/dataset-profiles/sales.csv", headers=_h("ws-a"))
    assert mine.status_code == 200 and "A-ONLY" in mine.text

    theirs = await client.get("/dataset-profiles/sales.csv", headers=_h("ws-b"))
    assert theirs.status_code == 404, "another workspace must not read this profile"
    assert "A-ONLY" not in theirs.text


@pytest.mark.asyncio
async def test_two_workspaces_can_hold_the_same_file_name_without_overwriting(client):
    for ws, marker in (("ws-a", "A"), ("ws-b", "B")):
        r = await client.post("/dataset-profiles/sales.csv", json={"dataset_name": marker, "profile": {"v": marker}}, headers=_h(ws))
        assert r.status_code == 200
    a = (await client.get("/dataset-profiles/sales.csv", headers=_h("ws-a"))).json()["profile"]
    b = (await client.get("/dataset-profiles/sales.csv", headers=_h("ws-b"))).json()["profile"]
    assert a["dataset_name"] == "A" and b["dataset_name"] == "B"
