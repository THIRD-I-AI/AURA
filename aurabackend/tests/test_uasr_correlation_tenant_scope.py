"""BUG-354: GET /uasr/correlation computed the incident over every tenant's sources and
filtered only source_ids afterwards, so a tenant saw other tenants' drift types and
timestamps, and could count their drifting sources by stepping min_sources."""
from __future__ import annotations

import os
import sys

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr import service  # noqa: E402
from uasr.metrics import HealingMetricTracker, RecoveryEvent  # noqa: E402
from uasr.models import DriftSeverity, DriftType, RecoveryStatus  # noqa: E402


def _request(tenant: str) -> Request:
    req = Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})
    req.state.user = {"sub": tenant}
    return req


@pytest.fixture()
def tracker(monkeypatch):
    t = HealingMetricTracker()
    monkeypatch.setattr(service, "_tracker", t)
    for source, drift_type in [("a::s1", DriftType.SCHEMA), ("a::s2", DriftType.SCHEMA),
                               ("b::s1", DriftType.SEMANTIC), ("b::s2", DriftType.SEMANTIC)]:
        t.record(RecoveryEvent(
            source_id=source, drift_type=drift_type, severity=DriftSeverity.MEDIUM,
            status=RecoveryStatus.FAILED, latency_seconds=0.0, recovery_id=f"r_{source}"))
    return t


@pytest.mark.asyncio
async def test_a_tenant_sees_only_its_own_drift(tracker):
    out = await service.get_correlation(_request("a"), window_seconds=60, min_sources=2)

    assert out["correlated"] is True
    assert out["source_ids"] == ["a::s1", "a::s2"]
    assert out["drift_types"] == [DriftType.SCHEMA.value], "another tenant's drift type leaked"


@pytest.mark.asyncio
async def test_other_tenants_sources_do_not_count_toward_the_threshold(tracker):
    # Tenant a has 2 drifting sources; with 4 fleet-wide, min_sources=3 used to say true.
    out = await service.get_correlation(_request("a"), window_seconds=60, min_sources=3)

    assert out == {"correlated": False}
