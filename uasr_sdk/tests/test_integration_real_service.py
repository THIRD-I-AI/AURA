"""
End-to-end integration test: the SDK driving the REAL UASR FastAPI app
in-process over an ASGI transport. No network server required.

Skipped automatically if the backend cannot be imported (e.g. the SDK is
installed standalone without ``aurabackend`` on the path).
"""
from __future__ import annotations

import os
import tempfile

import httpx
import pytest

# --- make the backend importable + writable before importing the app ---
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "aurabackend"))
_TMPDIR = tempfile.mkdtemp(prefix="uasr_sdk_it_")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-sdk-integration")
os.environ["METADATA_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMPDIR}/metadata.db"
os.environ["AURA_LEDGER_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMPDIR}/audit.db"
os.environ.setdefault("UASR_STATE_BACKEND", "memory")
os.environ.setdefault("UASR_REPAIR_BACKEND", "none")

if _REPO not in os.sys.path:
    os.sys.path.insert(0, _REPO)

app = pytest.importorskip("uasr.service", reason="aurabackend not importable").app  # type: ignore

from uasr_client import AsyncUASRClient  # noqa: E402


@pytest.fixture
async def live_client():
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncUASRClient("http://itest", transport=transport) as c:
            yield c


async def test_full_healing_loop(live_client: AsyncUASRClient) -> None:
    # 1. deployment endpoint is reachable
    dep = await live_client.deployment()
    assert dep.state_backend == "memory"

    # 2. register a baseline of healthy numeric data
    healthy = [{"amount": float(x)} for x in range(100)]
    base = await live_client.register_baseline("itest_src", healthy)
    assert base.status == "registered"
    assert base.row_count == 100

    # 3. a healthy batch passes clean
    clean = await live_client.ingest("itest_src", healthy, batch_id="clean1")
    assert clean.drift_detected is False

    # 4. a x100 unit-bug batch is detected as critical drift
    drifted = [{"amount": float(x) * 100} for x in range(100)]
    res = await live_client.ingest("itest_src", drifted, batch_id="drift1")
    assert res.drift_detected is True
    assert res.severity == "critical"
    assert res.drift_event_id

    # 5. the drift event was persisted and the source is tracked
    status = await live_client.drift_status("itest_src")
    events = status.get("events", status) if isinstance(status, dict) else status
    assert events  # at least one event recorded

    srcs = await live_client.sources()
    assert any(s.source_id == "itest_src" and s.has_active_baseline for s in srcs)

    # 6. metrics compute without error
    m = await live_client.metrics()
    assert isinstance(m.model_dump(), dict)
