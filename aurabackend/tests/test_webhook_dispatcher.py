"""
AURA Webhook Dispatcher Tests
===============================
Tests for subscription CRUD, event classification, delivery logic (mocked httpx),
HMAC signing, retry behaviour, and the build_payload helper.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.streaming_manager import StreamEvent


# ── Import targets after sys.path fix ─────────────────────────────

# We need to patch _load before WebhookDispatcher.__init__ reads from disk
with patch("shared.webhook_dispatcher.WebhookDispatcher._load"):
    from shared.webhook_dispatcher import (
        DeliveryRecord,
        WebhookDispatcher,
        WebhookSubscription,
        _classify,
    )


# ── Helpers ────────────────────────────────────────────────────────

def _make_dispatcher() -> WebhookDispatcher:
    """Create a dispatcher that skips file I/O."""
    with patch.object(WebhookDispatcher, "_load"):
        with patch.object(WebhookDispatcher, "_save"):
            d = WebhookDispatcher()
    return d


# ── WebhookSubscription ──────────────────────────────────────────

class TestWebhookSubscription:
    def test_matches_exact(self):
        sub = WebhookSubscription(id="1", url="http://x", events=["pipeline.complete"])
        assert sub.matches("pipeline.complete") is True
        assert sub.matches("pipeline.failed") is False

    def test_matches_wildcard(self):
        sub = WebhookSubscription(id="1", url="http://x", events=["*"])
        assert sub.matches("anything") is True
        assert sub.matches("pipeline.complete") is True

    def test_matches_prefix_wildcard(self):
        sub = WebhookSubscription(id="1", url="http://x", events=["agent.*"])
        assert sub.matches("agent.complete") is True
        assert sub.matches("agent.failed") is True
        assert sub.matches("pipeline.complete") is False

    def test_matches_multi_pattern(self):
        sub = WebhookSubscription(
            id="1", url="http://x", events=["pipeline.complete", "agent.*"]
        )
        assert sub.matches("pipeline.complete") is True
        assert sub.matches("agent.failed") is True
        assert sub.matches("uasr.drift") is False

    def test_to_dict(self):
        sub = WebhookSubscription(id="abc", url="http://x", events=["*"], secret="s3cret")
        d = sub.to_dict()
        assert d["id"] == "abc"
        assert d["url"] == "http://x"
        assert d["secret"] == "s3cret"  # raw dict; redaction is API-layer

    def test_default_values(self):
        sub = WebhookSubscription(id="1", url="http://x", events=["*"])
        assert sub.active is True
        assert sub.retries == 3
        assert sub.headers == {}
        assert sub.description == ""
        assert sub.secret is None


# ── Event classifier ──────────────────────────────────────────────

class TestClassify:
    def test_pipeline_complete(self):
        ev = StreamEvent(topic="pipeline:job1", event_type="complete", payload={})
        assert _classify(ev) == "pipeline.complete"

    def test_pipeline_error(self):
        ev = StreamEvent(topic="pipeline:job1", event_type="error", payload={})
        assert _classify(ev) == "pipeline.failed"

    def test_agent_complete(self):
        ev = StreamEvent(topic="agent:run1", event_type="complete", payload={})
        assert _classify(ev) == "agent.complete"

    def test_agent_error(self):
        ev = StreamEvent(topic="agent:run1", event_type="error", payload={})
        assert _classify(ev) == "agent.failed"

    def test_etl_complete(self):
        ev = StreamEvent(topic="etl:job1", event_type="complete", payload={})
        assert _classify(ev) == "etl.complete"

    def test_query_complete(self):
        ev = StreamEvent(topic="query:q1", event_type="complete", payload={})
        assert _classify(ev) == "query.complete"

    def test_upload_error(self):
        ev = StreamEvent(topic="upload:f1", event_type="error", payload={})
        assert _classify(ev) == "upload.failed"

    def test_pipeline_progress_ignored(self):
        ev = StreamEvent(topic="pipeline:job1", event_type="progress", payload={})
        assert _classify(ev) is None

    def test_uasr_drift(self):
        ev = StreamEvent(
            topic="uasr:metrics", event_type="data",
            payload={"drift_detected": True}
        )
        assert _classify(ev) == "uasr.drift"

    def test_uasr_no_drift(self):
        ev = StreamEvent(
            topic="uasr:metrics", event_type="data",
            payload={"drift_detected": False}
        )
        assert _classify(ev) is None

    def test_uasr_complete(self):
        ev = StreamEvent(topic="uasr:src1", event_type="complete", payload={})
        assert _classify(ev) == "uasr.recovered"

    def test_uasr_progress_ignored(self):
        ev = StreamEvent(topic="uasr:src1", event_type="progress", payload={})
        assert _classify(ev) is None

    def test_hook_fired(self):
        ev = StreamEvent(topic="hooks:slug1", event_type="complete", payload={})
        assert _classify(ev) == "hook.fired"

    def test_system_degraded(self):
        ev = StreamEvent(
            topic="system:health", event_type="data",
            payload={"overall": "degraded"}
        )
        assert _classify(ev) == "system.degraded"

    def test_system_healthy_ignored(self):
        ev = StreamEvent(
            topic="system:health", event_type="data",
            payload={"overall": "healthy"}
        )
        assert _classify(ev) is None

    def test_unknown_namespace_returns_none(self):
        ev = StreamEvent(topic="random:stuff", event_type="complete", payload={})
        assert _classify(ev) is None


# ── Dispatcher CRUD ───────────────────────────────────────────────

class TestDispatcherCRUD:
    def test_register(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://example.com/hook", ["pipeline.complete"])
        assert sub.url == "http://example.com/hook"
        assert sub.events == ["pipeline.complete"]
        assert sub.active is True
        assert sub.id in [s.id for s in d.list()]

    def test_register_with_secret(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://x", ["*"], secret="my-secret", retries=5)
        assert sub.secret == "my-secret"
        assert sub.retries == 5

    def test_register_clamps_retries(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://x", ["*"], retries=99)
        assert sub.retries == 10  # clamped

    def test_get(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://x", ["*"])
        assert d.get(sub.id) is sub
        assert d.get("nonexistent") is None

    def test_list(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            d.register("http://a", ["*"])
            d.register("http://b", ["pipeline.*"])
        assert len(d.list()) == 2

    def test_update(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://x", ["*"])
            updated = d.update(sub.id, url="http://new-url", retries=1)
        assert updated is not None
        assert updated.url == "http://new-url"
        assert updated.retries == 1

    def test_update_nonexistent(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            assert d.update("nope", url="http://x") is None

    def test_delete(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            sub = d.register("http://x", ["*"])
            assert d.delete(sub.id) is True
            assert d.get(sub.id) is None

    def test_delete_nonexistent(self):
        d = _make_dispatcher()
        with patch.object(d, "_save"):
            assert d.delete("nope") is False

    def test_deliveries_empty(self):
        d = _make_dispatcher()
        assert d.deliveries() == []


# ── build_payload ─────────────────────────────────────────────────

class TestBuildPayload:
    def test_structure(self):
        ev = StreamEvent(
            topic="pipeline:job1", event_type="complete",
            payload={"result": "ok"}, event_id="ev123",
        )
        payload = WebhookDispatcher._build_payload("pipeline.complete", ev)
        assert payload["id"] == "ev123"
        assert payload["event"] == "pipeline.complete"
        assert payload["topic"] == "pipeline:job1"
        assert payload["data"] == {"result": "ok"}
        assert "timestamp" in payload


# ── Delivery (mocked httpx) ───────────────────────────────────────

class TestDelivery:
    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        d = _make_dispatcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        d._client = mock_client

        with patch.object(d, "_save"):
            sub = d.register("http://target/hook", ["*"])

        payload = {"id": "e1", "event": "test", "topic": "t", "timestamp": "now", "data": {}}

        with patch("shared.webhook_dispatcher.streaming_manager") as mock_sm:
            mock_sm.publish = AsyncMock()
            await d._deliver(sub, "test.ping", payload)

        assert len(d.deliveries()) == 1
        rec = d.deliveries()[0]
        assert rec.status == "success"
        assert rec.http_status == 200
        assert rec.attempts == 1

    @pytest.mark.asyncio
    async def test_failed_delivery_records_error(self):
        d = _make_dispatcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        d._client = mock_client

        with patch.object(d, "_save"):
            sub = d.register("http://target/hook", ["*"], retries=0)

        payload = {"id": "e1", "event": "test", "topic": "t", "timestamp": "now", "data": {}}

        with patch("shared.webhook_dispatcher.streaming_manager") as mock_sm:
            mock_sm.publish = AsyncMock()
            await d._deliver(sub, "test.ping", payload)

        rec = d.deliveries()[0]
        assert rec.status == "failed"
        assert rec.http_status == 500
        assert rec.error == "HTTP 500"

    @pytest.mark.asyncio
    async def test_hmac_signature_included(self):
        d = _make_dispatcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        d._client = mock_client

        with patch.object(d, "_save"):
            sub = d.register("http://target", ["*"], secret="test-secret", retries=0)

        payload = {"id": "e1", "event": "test", "topic": "t", "timestamp": "now", "data": {}}

        with patch("shared.webhook_dispatcher.streaming_manager") as mock_sm:
            mock_sm.publish = AsyncMock()
            await d._deliver(sub, "test.ping", payload)

        # Verify the post was called with signature header
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "X-AURA-Signature" in headers
        assert headers["X-AURA-Signature"].startswith("sha256=")

    @pytest.mark.asyncio
    async def test_network_error_retries(self):
        d = _make_dispatcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("network down"))
        d._client = mock_client

        with patch.object(d, "_save"):
            sub = d.register("http://target", ["*"], retries=1)

        payload = {"id": "e1", "event": "test", "topic": "t", "timestamp": "now", "data": {}}

        with patch("shared.webhook_dispatcher.streaming_manager") as mock_sm:
            mock_sm.publish = AsyncMock()
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await d._deliver(sub, "test.ping", payload)

        rec = d.deliveries()[0]
        assert rec.status == "failed"
        assert rec.attempts == 2  # 1 initial + 1 retry

    @pytest.mark.asyncio
    async def test_fire_test_known_sub(self):
        d = _make_dispatcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        d._client = mock_client

        with patch.object(d, "_save"):
            sub = d.register("http://target", ["*"], retries=0)

        with patch("shared.webhook_dispatcher.streaming_manager") as mock_sm:
            mock_sm.publish = AsyncMock()
            rec = await d.fire_test(sub.id)

        assert rec is not None
        assert rec.status == "success"

    @pytest.mark.asyncio
    async def test_fire_test_unknown_sub(self):
        d = _make_dispatcher()
        result = await d.fire_test("nonexistent")
        assert result is None


# ── DeliveryRecord ────────────────────────────────────────────────

class TestDeliveryRecord:
    def test_fields(self):
        rec = DeliveryRecord(
            id="d1", subscription_id="s1", event_type="test",
            url="http://x", status="success", http_status=200,
            attempts=1, error=None,
        )
        assert rec.status == "success"
        assert rec.http_status == 200
        assert rec.error is None
        assert rec.timestamp  # auto-generated
