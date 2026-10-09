"""BUG-375: POST /analyze/results ran the InsightsEngine's statistics over every posted
row inline on the single-worker event loop, with no bound on the row count."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

ROWS = [{"region": "east" if i % 2 else "west", "revenue": float(i)} for i in range(50)]


def _client():
    from api_gateway.main import app
    return TestClient(app)


def test_more_rows_than_the_result_cap_are_refused(monkeypatch):
    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", "10")
    r = _client().post("/api/v1/analyze/results", params={"query": "q"}, json={"results": ROWS})
    assert r.status_code == 413, r.text


def test_the_engine_runs_off_the_event_loop(monkeypatch):
    from insights_service.engine import InsightsEngine

    real, seen = InsightsEngine.analyze, []

    def _spy(self, *a, **k):
        try:
            asyncio.get_running_loop()
            seen.append("on the event loop")
        except RuntimeError:
            seen.append("in a worker thread")
        return real(self, *a, **k)

    monkeypatch.setattr(InsightsEngine, "analyze", _spy)
    r = _client().post("/api/v1/analyze/results", params={"query": "q"}, json={"results": ROWS})

    assert r.status_code == 200, r.text
    assert seen == ["in a worker thread"]
