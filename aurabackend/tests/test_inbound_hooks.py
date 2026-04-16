"""
AURA Inbound Hooks Tests
==========================
Tests for InboundHook model, InboundHookRegistry CRUD, persistence,
slug uniqueness, and fire recording.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import mock_open, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch _load so importing the module doesn't read from disk
with patch("shared.inbound_hooks.InboundHookRegistry._load"):
    from shared.inbound_hooks import InboundHook, InboundHookRegistry


# ── Helpers ────────────────────────────────────────────────────────

def _make_registry() -> InboundHookRegistry:
    """Create a registry that skips file I/O."""
    with patch.object(InboundHookRegistry, "_load"):
        with patch.object(InboundHookRegistry, "_save"):
            r = InboundHookRegistry()
    return r


# ── InboundHook model ─────────────────────────────────────────────

class TestInboundHook:
    def test_defaults(self):
        h = InboundHook(id="1", slug="my-hook", kind="pipeline", target="pipe-123")
        assert h.active is True
        assert h.secret is None
        assert h.description == ""
        assert h.pass_payload_as is None
        assert h.last_fired_at is None
        assert h.fire_count == 0
        assert h.created_at  # auto-generated

    def test_to_dict_redacts_secret(self):
        h = InboundHook(id="1", slug="s", kind="agent", target="t", secret="mysecret")
        d = h.to_dict()
        assert d["secret"] == "***redacted***"
        assert d["has_secret"] is True

    def test_to_dict_no_secret(self):
        h = InboundHook(id="1", slug="s", kind="pipeline", target="t")
        d = h.to_dict()
        assert d["has_secret"] is False

    def test_all_fields(self):
        h = InboundHook(
            id="abc", slug="deploy", kind="agent", target="prompt-template",
            secret="s", active=False, description="Deploy hook",
            pass_payload_as="body", fire_count=5,
        )
        assert h.id == "abc"
        assert h.slug == "deploy"
        assert h.kind == "agent"
        assert h.target == "prompt-template"
        assert h.active is False
        assert h.pass_payload_as == "body"
        assert h.fire_count == 5


# ── Registry CRUD ─────────────────────────────────────────────────

class TestRegistryCRUD:
    def test_register_pipeline(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("my-slug", "pipeline", "pipe-123")
        assert h.slug == "my-slug"
        assert h.kind == "pipeline"
        assert h.target == "pipe-123"
        assert h.id  # auto-generated
        assert len(r.list()) == 1

    def test_register_agent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("agent-hook", "agent", "Analyse {{data}}")
        assert h.kind == "agent"

    def test_register_invalid_kind(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            with pytest.raises(ValueError, match="kind must be"):
                r.register("bad", "invalid_kind", "target")

    def test_register_duplicate_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("unique-slug", "pipeline", "t")
            with pytest.raises(ValueError, match="already registered"):
                r.register("unique-slug", "agent", "t2")

    def test_register_with_optional_fields(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register(
                "hook1", "pipeline", "pipe-1",
                secret="sec", description="A hook", pass_payload_as="data",
            )
        assert h.secret == "sec"
        assert h.description == "A hook"
        assert h.pass_payload_as == "data"

    def test_get_by_id(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("s1", "pipeline", "t")
        assert r.get(h.id) is h
        assert r.get("nonexistent") is None

    def test_by_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("my-slug", "pipeline", "t")
        assert r.by_slug("my-slug") is h
        assert r.by_slug("other") is None

    def test_list(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("s1", "pipeline", "t1")
            r.register("s2", "agent", "t2")
        hooks = r.list()
        assert len(hooks) == 2
        slugs = {h.slug for h in hooks}
        assert slugs == {"s1", "s2"}

    def test_delete(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("s1", "pipeline", "t")
            assert r.delete(h.id) is True
        assert r.get(h.id) is None
        assert r.by_slug("s1") is None

    def test_delete_nonexistent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            assert r.delete("nope") is False


# ── Update ────────────────────────────────────────────────────────

class TestRegistryUpdate:
    def test_update_fields(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("s1", "pipeline", "t")
            updated = r.update(h.id, target="new-target", description="updated")
        assert updated is not None
        assert updated.target == "new-target"
        assert updated.description == "updated"

    def test_update_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("old-slug", "pipeline", "t")
            updated = r.update(h.id, slug="new-slug")
        assert updated.slug == "new-slug"
        assert r.by_slug("new-slug") is updated
        assert r.by_slug("old-slug") is None

    def test_update_slug_conflict(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("slug-a", "pipeline", "t1")
            h2 = r.register("slug-b", "pipeline", "t2")
            with pytest.raises(ValueError, match="already registered"):
                r.update(h2.id, slug="slug-a")

    def test_update_nonexistent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            assert r.update("nope", target="x") is None

    def test_update_ignores_none_values(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("s1", "pipeline", "original-target")
            r.update(h.id, target=None)
        assert h.target == "original-target"  # unchanged


# ── Fire recording ────────────────────────────────────────────────

class TestRecordFire:
    def test_increments_count(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("s1", "pipeline", "t")
            assert h.fire_count == 0
            r.record_fire(h)
            assert h.fire_count == 1
            assert h.last_fired_at is not None
            r.record_fire(h)
            assert h.fire_count == 2


# ── Persistence (mocked I/O) ─────────────────────────────────────

class TestPersistence:
    def test_load_from_file(self, tmp_path):
        store = tmp_path / "inbound.json"
        data = [
            {
                "id": "h1", "slug": "hook-one", "kind": "pipeline",
                "target": "pipe-1", "secret": None, "active": True,
                "description": "", "pass_payload_as": None,
                "last_fired_at": None, "fire_count": 0,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        store.write_text(json.dumps(data))

        with patch("shared.inbound_hooks._STORE_PATH", str(store)):
            reg = InboundHookRegistry()

        assert len(reg.list()) == 1
        assert reg.by_slug("hook-one") is not None

    def test_load_missing_file(self, tmp_path):
        with patch("shared.inbound_hooks._STORE_PATH", str(tmp_path / "missing.json")):
            reg = InboundHookRegistry()
        assert reg.list() == []

    def test_save(self, tmp_path):
        store = tmp_path / "inbound.json"
        with patch("shared.inbound_hooks._STORE_PATH", str(store)):
            with patch("shared.inbound_hooks._DATA_DIR", str(tmp_path)):
                reg = InboundHookRegistry()
                reg.register("s1", "pipeline", "t")

        assert store.exists()
        saved = json.loads(store.read_text())
        assert len(saved) == 1
        assert saved[0]["slug"] == "s1"
