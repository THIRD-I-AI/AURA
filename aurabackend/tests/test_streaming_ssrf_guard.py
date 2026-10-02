"""BUG-287: the streaming webhook sink and websocket source connected to whatever URL
the pipeline definition named -- loopback, link-local metadata, private ranges -- so a
tenant could make the gateway call, and read from, its own internal network."""
from __future__ import annotations

import httpx
import pytest

from pipeline.streaming.sinks.webhook_sink import WebhookSink
from pipeline.streaming.sources.websocket_source import WebSocketSource
from shared import ssrf

INTERNAL_HTTP = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8009/uasr/metrics",
    "http://localhost:8000/health",
    "http://10.0.0.5/hook",
    "http://[::1]/hook",
    "file:///etc/passwd",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", INTERNAL_HTTP)
async def test_webhook_sink_refuses_to_start_on_an_internal_url(url):
    sink = WebhookSink(config={"url": url})

    with pytest.raises(ValueError, match="public http"):
        await sink.start()

    assert sink._client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "ws://127.0.0.1:8009/stream", "ws://localhost/x", "ws://169.254.169.254/", "wss://192.168.1.10/feed",
    "http://8.8.8.8/not-a-websocket",
])
async def test_websocket_source_refuses_to_start_on_an_internal_url(url):
    source = WebSocketSource({"url": url})

    with pytest.raises(ValueError, match="public ws"):
        await source.start()

    assert source._reader_task is None


@pytest.mark.asyncio
async def test_a_url_that_turns_internal_after_start_is_not_posted_to(monkeypatch):
    """The name passed the check at start() and was then re-pointed (DNS rebinding)."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200)

    verdicts = iter([True, True, False])
    monkeypatch.setattr(ssrf, "is_public_url", lambda url, schemes=("http", "https"): next(verdicts))

    sink = WebhookSink(config={"url": "https://hooks.example.com/aura"})
    await sink.start()
    sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await sink._post({"n": 1})
        await sink._post({"n": 2})
    finally:
        await sink.stop()

    assert sent == ["https://hooks.example.com/aura"]


def test_retries_are_capped():
    assert WebhookSink(config={"url": "https://hooks.example.com/a", "retries": 10_000})._retries == 5


@pytest.mark.parametrize("url,schemes,expected", [
    ("https://8.8.8.8/x", ("http", "https"), True),
    ("ws://8.8.8.8/x", ("ws", "wss"), True),
    ("ws://8.8.8.8/x", ("http", "https"), False),
    ("http://0.0.0.0/", ("http", "https"), False),
    ("http://no-such-host.invalid/", ("http", "https"), False),
])
def test_is_public_url(url, schemes, expected):
    assert ssrf.is_public_url(url, schemes) is expected
