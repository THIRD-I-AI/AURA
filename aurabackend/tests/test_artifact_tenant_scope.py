"""BUG-208: artifact read routes must not serve one tenant's artifact to another."""
import pytest
from fastapi import HTTPException

pytest.importorskip("econml", reason="counterfactual_service imports need the causal stack")

from counterfactual_service import main as cf  # noqa: E402

HASH = "a" * 64
DOC_A = {"record_hash": HASH, "tenant_id": "tenant-a", "findings": [{"evidence": "raw-secret"}]}


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setattr(cf.persistence, "read_artifact", lambda h: dict(DOC_A) if h == HASH else None)


@pytest.mark.asyncio
async def test_owner_can_read_its_artifact():
    art = await cf.get_artifact(HASH, user={"sub": "u1", "org_id": "tenant-a"})
    assert art["findings"][0]["evidence"] == "raw-secret"


@pytest.mark.asyncio
async def test_other_tenant_gets_404_not_the_artifact():
    with pytest.raises(HTTPException) as e:
        await cf.get_artifact(HASH, user={"sub": "u2", "org_id": "tenant-b"})
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_caller_cannot_read_a_tenants_artifact():
    with pytest.raises(HTTPException) as e:
        await cf.get_artifact(HASH, user=None)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_pdf_route_applies_the_same_check(monkeypatch):
    monkeypatch.setattr(cf.pdf_renderer, "render_pdf", lambda art: b"%PDF")
    with pytest.raises(HTTPException) as e:
        await cf.get_artifact_pdf(HASH, user={"sub": "u2", "org_id": "tenant-b"})
    assert e.value.status_code == 404
    ok = await cf.get_artifact_pdf(HASH, user={"sub": "u1", "org_id": "tenant-a"})
    assert ok.body == b"%PDF"


@pytest.mark.asyncio
async def test_missing_hash_is_indistinguishable_from_cross_tenant():
    with pytest.raises(HTTPException) as e:
        await cf.get_artifact("b" * 64, user={"sub": "u2", "org_id": "tenant-b"})
    assert e.value.status_code == 404
