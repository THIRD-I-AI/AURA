"""
AURA Vault Client Tests
========================
Tests for the AuraVault domain facade over DatabaseAdapter.
All database calls are mocked — no real DB connection needed.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database_adapter import AdapterConfig, BackendType, DatabaseAdapter, DuckDBAdapter, _adapter_cache


# ── Helpers ────────────────────────────────────────────────────────

def _mock_adapter() -> MagicMock:
    """Create a fully mocked DatabaseAdapter."""
    m = AsyncMock(spec=DatabaseAdapter)
    m.connect = AsyncMock(return_value=True)
    m.disconnect = AsyncMock(return_value=True)
    m.health_check = AsyncMock(return_value={"ok": True})
    m.capabilities = AsyncMock(return_value={"relational": True, "vector": False, "spatial": False})
    m.execute_query = AsyncMock(return_value=[])
    m.execute_write = AsyncMock(return_value=1)
    m.vector_search = AsyncMock(return_value=[])
    m.store_vector = AsyncMock(return_value="vec-id-1")
    m.store_point = AsyncMock(return_value="point-id-1")
    m.spatial_query = AsyncMock(return_value=[])
    return m


# ── Lifecycle ─────────────────────────────────────────────────────

class TestVaultLifecycle:
    @pytest.mark.asyncio
    async def test_connect(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        result = await v.connect()
        assert result is True
        adapter.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        result = await v.disconnect()
        assert result is True
        adapter.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_health(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        h = await v.health()
        assert h["ok"] is True

    @pytest.mark.asyncio
    async def test_capabilities(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        caps = await v.capabilities()
        assert caps["relational"] is True


# ── Regular data ──────────────────────────────────────────────────

class TestRegularData:
    @pytest.mark.asyncio
    async def test_get_top_customers(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [
            {"email": "a@b.com", "display_name": "Alice", "total_spend": 500},
        ]
        v = AuraVault(adapter=adapter)
        result = await v.get_top_customers(limit=5)
        assert len(result) == 1
        assert result[0]["email"] == "a@b.com"
        adapter.execute_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_user_found(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"user_id": "u1", "email": "a@b.com"}]
        v = AuraVault(adapter=adapter)
        user = await v.get_user("u1")
        assert user is not None
        assert user["email"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = []
        v = AuraVault(adapter=adapter)
        assert await v.get_user("missing") is None

    @pytest.mark.asyncio
    async def test_create_user(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"user_id": "new-id"}]
        v = AuraVault(adapter=adapter)
        uid = await v.create_user("test@example.com", "Test User")
        assert uid == "new-id"

    @pytest.mark.asyncio
    async def test_create_user_failure(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = []
        v = AuraVault(adapter=adapter)
        assert await v.create_user("x@y.com") is None

    @pytest.mark.asyncio
    async def test_register_data_source(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"source_id": "src-1"}]
        v = AuraVault(adapter=adapter)
        sid = await v.register_data_source("u1", "postgres", "My DB", {"host": "x"})
        assert sid == "src-1"

    @pytest.mark.asyncio
    async def test_log_audit(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        await v.log_audit("u1", "login", "auth", {"ip": "127.0.0.1"})
        adapter.execute_write.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_query(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"query_id": "q-1"}]
        v = AuraVault(adapter=adapter)
        qid = await v.save_query("u1", "Test", "SELECT 1")
        assert qid == "q-1"


# ── Agent memory ──────────────────────────────────────────────────

class TestAgentMemory:
    @pytest.mark.asyncio
    async def test_store_memory_with_embedding(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        mid = await v.store_memory("s1", "u1", "user", "hello", embedding=[0.1, 0.2])
        assert mid == "vec-id-1"
        adapter.store_vector.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_memory_without_embedding(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"memory_id": "mem-1"}]
        v = AuraVault(adapter=adapter)
        mid = await v.store_memory("s1", "u1", "user", "hello")
        assert mid == "mem-1"

    @pytest.mark.asyncio
    async def test_recall_memory(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.vector_search.return_value = [{"content": "past msg"}]
        v = AuraVault(adapter=adapter)
        results = await v.recall_memory([0.1, 0.2])
        assert len(results) == 1


# ── Image / AI embeddings ────────────────────────────────────────

class TestImageEmbeddings:
    @pytest.mark.asyncio
    async def test_store_image(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        iid = await v.store_image("photo.jpg", "https://x/photo.jpg", [0.5, 0.6],
                                  user_id="u1", labels=["cat"], metadata={"size": 1024})
        assert iid == "vec-id-1"
        adapter.store_vector.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_similar_images(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.vector_search.return_value = [{"file_name": "a.jpg"}]
        v = AuraVault(adapter=adapter)
        results = await v.find_similar_images([0.1, 0.2])
        assert len(results) == 1


# ── Generic vector store ─────────────────────────────────────────

class TestGenericVectorStore:
    @pytest.mark.asyncio
    async def test_store_embedding(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        vid = await v.store_embedding("docs", [0.1], content="text", metadata={"k": "v"})
        assert vid == "vec-id-1"

    @pytest.mark.asyncio
    async def test_search_embeddings(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.vector_search.return_value = [
            {"collection": "docs", "content": "a"},
            {"collection": "other", "content": "b"},
            {"collection": "docs", "content": "c"},
        ]
        v = AuraVault(adapter=adapter)
        results = await v.search_embeddings("docs", [0.1], limit=10)
        assert len(results) == 2
        assert all(r["collection"] == "docs" for r in results)


# ── VR / 4D Spatial ──────────────────────────────────────────────

class TestVRSpatial:
    @pytest.mark.asyncio
    async def test_store_vr_frame(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        tid = await v.store_vr_frame("u1", 1.0, 2.0, 3.0,
                                     velocity=[0.1, 0.2, 0.3],
                                     session_id="sess1",
                                     metadata={"device": "Quest"})
        assert tid == "point-id-1"
        adapter.store_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_user_vr_path(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.spatial_query.return_value = [{"telemetry_id": "t1"}]
        v = AuraVault(adapter=adapter)
        result = await v.get_user_vr_path("u1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_find_users_in_area(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.spatial_query.return_value = [{"user_id": "u2"}]
        v = AuraVault(adapter=adapter)
        result = await v.find_users_in_area(1.0, 2.0, 3.0, radius_meters=50)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_link_vr_to_purchase(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"vr_position": "POINT(1 2 3)", "amount": 99}]
        v = AuraVault(adapter=adapter)
        result = await v.link_vr_to_purchase("u1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_create_vr_environment(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        adapter.execute_query.return_value = [{"env_id": "env-1"}]
        v = AuraVault(adapter=adapter)
        eid = await v.create_vr_environment("TestEnv", config={"lighting": "ambient"})
        assert eid == "env-1"

    @pytest.mark.asyncio
    async def test_store_vr_object(self):
        from shared.vault_client import AuraVault
        adapter = _mock_adapter()
        v = AuraVault(adapter=adapter)
        oid = await v.store_vr_object("env-1", "cube", 1.0, 2.0, 3.0,
                                      label="Box", properties={"color": "red"})
        assert oid == "point-id-1"
