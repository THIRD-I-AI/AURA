"""BUG-316: register() clamped a webhook's retries to 0..10 but update() did not, so a
PATCH could set a million retries (a delivery task alive for hours per event) or a
negative count (delivery silently never attempted)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.webhook_dispatcher import WebhookDispatcher


def _dispatcher() -> WebhookDispatcher:
    with patch.object(WebhookDispatcher, "_load"), patch.object(WebhookDispatcher, "_save"):
        return WebhookDispatcher()


@pytest.mark.parametrize("requested,stored", [(1_000_000, 10), (-5, 0), (11, 10), (4, 4), (0, 0)])
def test_update_clamps_retries(requested, stored):
    d = _dispatcher()
    with patch.object(d, "_save"):
        sub = d.register("ws-a", "http://x", ["*"])
        updated = d.update(sub.id, "ws-a", retries=requested)

    assert updated.retries == stored


def test_update_without_retries_leaves_them_alone():
    d = _dispatcher()
    with patch.object(d, "_save"):
        sub = d.register("ws-a", "http://x", ["*"], retries=7)
        updated = d.update(sub.id, "ws-a", description="renamed")

    assert updated.retries == 7
