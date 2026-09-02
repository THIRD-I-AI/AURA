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
            h = r.register("ws-a", "my-slug", "pipeline", "pipe-123")
        assert h.slug == "my-slug"
        assert h.kind == "pipeline"
        assert h.target == "pipe-123"
        assert h.id  # auto-generated
        assert len(r.list("ws-a")) == 1

    def test_register_agent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "agent-hook", "agent", "Analyse {{data}}")
        assert h.kind == "agent"

    def test_register_invalid_kind(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            with pytest.raises(ValueError, match="kind must be"):
                r.register("ws-a", "bad", "invalid_kind", "target")

    def test_register_duplicate_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("ws-a", "unique-slug", "pipeline", "t")
            with pytest.raises(ValueError, match="already registered"):
                r.register("ws-a", "unique-slug", "agent", "t2")

    def test_register_with_optional_fields(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register(
                "ws-a", "hook1", "pipeline", "pipe-1",
                secret="sec", description="A hook", pass_payload_as="data",
            )
        assert h.secret == "sec"
        assert h.description == "A hook"
        assert h.pass_payload_as == "data"

    def test_get_by_id(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "s1", "pipeline", "t")
        assert r.get(h.id, "ws-a") is h
        assert r.get("nonexistent", "ws-a") is None

    def test_by_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "my-slug", "pipeline", "t")
        assert r.by_slug("my-slug") is h
        assert r.by_slug("other") is None

    def test_list(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("ws-a", "s1", "pipeline", "t1")
            r.register("ws-a", "s2", "agent", "t2")
        hooks = r.list("ws-a")
        assert len(hooks) == 2
        slugs = {h.slug for h in hooks}
        assert slugs == {"s1", "s2"}

    def test_delete(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "s1", "pipeline", "t")
            assert r.delete(h.id, "ws-a") is True
        assert r.get(h.id, "ws-a") is None
        assert r.by_slug("s1") is None

    def test_delete_nonexistent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            assert r.delete("nope", "ws-a") is False


# ── Update ────────────────────────────────────────────────────────

class TestRegistryUpdate:
    def test_update_fields(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "s1", "pipeline", "t")
            updated = r.update(h.id, "ws-a", target="new-target", description="updated")
        assert updated is not None
        assert updated.target == "new-target"
        assert updated.description == "updated"

    def test_update_slug(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "old-slug", "pipeline", "t")
            updated = r.update(h.id, "ws-a", slug="new-slug")
        assert updated.slug == "new-slug"
        assert r.by_slug("new-slug") is updated
        assert r.by_slug("old-slug") is None

    def test_update_slug_conflict(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("ws-a", "slug-a", "pipeline", "t1")
            h2 = r.register("ws-a", "slug-b", "pipeline", "t2")
            with pytest.raises(ValueError, match="already registered"):
                r.update(h2.id, "ws-a", slug="slug-a")

    def test_update_nonexistent(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            assert r.update("nope", "ws-a", target="x") is None

    def test_update_ignores_none_values(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "s1", "pipeline", "original-target")
            r.update(h.id, "ws-a", target=None)
        assert h.target == "original-target"  # unchanged


# ── Fire recording ────────────────────────────────────────────────

class TestRecordFire:
    def test_increments_count(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("ws-a", "s1", "pipeline", "t")
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
                "workspace_id": "ws-a",
            }
        ]
        store.write_text(json.dumps(data))

        with patch("shared.inbound_hooks._STORE_PATH", str(store)):
            reg = InboundHookRegistry()

        assert len(reg.list("ws-a")) == 1
        assert reg.by_slug("hook-one") is not None

    def test_load_missing_file(self, tmp_path):
        with patch("shared.inbound_hooks._STORE_PATH", str(tmp_path / "missing.json")):
            reg = InboundHookRegistry()
        assert reg.list("ws-a") == []

    def test_save(self, tmp_path):
        store = tmp_path / "inbound.json"
        with patch("shared.inbound_hooks._STORE_PATH", str(store)):
            with patch("shared.inbound_hooks._DATA_DIR", str(tmp_path)):
                reg = InboundHookRegistry()
                reg.register("ws-a", "s1", "pipeline", "t")

        assert store.exists()
        saved = json.loads(store.read_text())
        assert len(saved) == 1
        assert saved[0]["slug"] == "s1"


# ── Cross-tenant isolation (BUG-016) ───────────────────────────────

class TestCrossTenantIsolation:
    """The registry used to be keyed only by bare hook_id, with no
    workspace filter at all -- any authenticated caller could list/read/
    edit/delete any other tenant's inbound hook. Pin the fix: a request
    scoped to the wrong workspace must come back empty/None/False, never
    the other tenant's data. `by_slug` stays intentionally unscoped --
    that's the public fire-trigger lookup, gated by the hook's own HMAC
    secret instead."""

    def test_list_does_not_leak_other_tenants(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            r.register("org-a", "slug-a", "pipeline", "t1")
            r.register("org-b", "slug-b", "pipeline", "t2")
        assert len(r.list("org-a")) == 1
        assert len(r.list("org-b")) == 1
        assert r.list("org-a")[0].slug == "slug-a"

    def test_get_returns_none_for_wrong_tenant(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("org-a", "secret-hook", "pipeline", "t")
        assert r.get(h.id, "org-b") is None
        assert r.get(h.id, "org-a") is h

    def test_update_is_rejected_for_wrong_tenant(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("org-a", "s1", "pipeline", "t")
            assert r.update(h.id, "org-b", target="hijacked") is None
        assert h.target == "t"  # unchanged

    def test_delete_is_rejected_for_wrong_tenant(self):
        r = _make_registry()
        with patch.object(r, "_save"):
            h = r.register("org-a", "s1", "pipeline", "t")
            assert r.delete(h.id, "org-b") is False
        assert r.get(h.id, "org-a") is not None  # still there
        assert r.by_slug("s1") is not None
