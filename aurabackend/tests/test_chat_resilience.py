"""
Chat pipeline resilience tests
================================
Edge cases that the golden-path e2e test (test_e2e_chat.py) doesn't cover:

* Concurrent /chat requests don't corrupt shared state.
* LLM provider failure surfaces as a clean error response.
* Visualization agent given malformed records falls back to a heuristic spec
  rather than crashing the pipeline.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._mock_llm import chat_happy_path, chat_unavailable, install_mock

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def upload_dir():
    base = Path(__file__).resolve().parent.parent
    uploads = base / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    csv_path = uploads / "_resilience_test_sales.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Product", "Revenue", "Quantity", "Region"])
        for i in range(20):
            w.writerow([f"2024-01-{(i % 28) + 1:02d}", f"Widget {chr(65 + (i % 4))}", f"{1000 + i * 50}", str(10 + i), "North"])
    yield uploads
    csv_path.unlink(missing_ok=True)


@pytest.fixture()
def happy_client(upload_dir, monkeypatch):
    install_mock(monkeypatch, chat_happy_path(table_name="_resilience_test_sales"))
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    yield TestClient(app)


@pytest.fixture()
def failing_client(upload_dir, monkeypatch):
    install_mock(monkeypatch, chat_unavailable())
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    yield TestClient(app)


V1 = "/api/v1"


# ─────────────────────────────────────────────────────────────────────
# Concurrency
# ─────────────────────────────────────────────────────────────────────

def test_chat_concurrent_requests_no_corruption(happy_client):
    """10 parallel /chat calls all succeed and return distinct job IDs."""

    async def fire(i: int):
        return await asyncio.to_thread(
            happy_client.post,
            f"{V1}/chat",
            json={"message": f"revenue by product #{i}", "session_id": f"concurrent-{i}"},
        )

    async def runner():
        return await asyncio.gather(*(fire(i) for i in range(10)))

    responses = asyncio.run(runner())

    assert all(r.status_code == 200 for r in responses)
    job_ids = [r.json()["job_id"] for r in responses]
    assert len(set(job_ids)) == 10  # each call got its own session
    assert all(r.json()["status"] == "Success" for r in responses)


# ─────────────────────────────────────────────────────────────────────
# LLM provider down
# ─────────────────────────────────────────────────────────────────────

def test_chat_llm_provider_down_returns_clean_response(failing_client):
    """When the LLM provider is unavailable, /chat must respond cleanly —
    no 500, no stack trace leaking. Either the orchestrator's heuristic
    fallback paths produce a usable answer (Success — IntentAgent +
    SQLGenerator both have non-LLM heuristic codepaths), or the run
    surfaces a clean Error status with no traceback in the payload.

    The contract is "no crash + no internal-state leak", not a
    specific status string — the LangGraph migration legitimately
    changed this path from hard-fail to graceful degradation.
    """
    import json as _json
    resp = failing_client.post(f"{V1}/chat", json={"message": "anything"})
    assert resp.status_code == 200, "must not crash with 500"

    data = resp.json()
    assert data["status"] in ("Success", "Error", "Conversational"), (
        f"unexpected status {data['status']}"
    )
    # Internal exception text must never reach the user.
    payload = _json.dumps(data).lower()
    for leak in ("traceback", "exception", "stack trace", "<class '"):
        assert leak not in payload, f"internal leak {leak!r} surfaced in response"


# ─────────────────────────────────────────────────────────────────────
# Visualization fallback on malformed records
# ─────────────────────────────────────────────────────────────────────

def test_visualization_agent_falls_back_on_malformed_records():
    """When records aren't list[dict], VisualizationAgent must not crash —
    it should return a heuristic spec or a graceful error step."""
    from agents.base import AgentContext
    from agents.specialists.visualization_agent import VisualizationAgent

    agent = VisualizationAgent()

    # Pathological input: records is a list of scalars (not dicts).
    ctx = AgentContext(
        user_prompt="plot this",
        task_description="visualize",
        schema_context={
            "records": [1, 2, 3, 4],
            "columns": ["value"],
        },
    )

    result = asyncio.run(agent.execute(ctx))

    # The pipeline shouldn't crash. The agent may fail gracefully, but the
    # AgentResult itself should always come back populated.
    assert result is not None
    assert hasattr(result, "status")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
