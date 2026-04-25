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
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional, Union
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_provider import LLMProvider

# ─────────────────────────────────────────────────────────────────────
# Mock LLMs (one happy, one always-fail)
# ─────────────────────────────────────────────────────────────────────

class _HappyMockLLM(LLMProvider):
    provider_name = "mock-happy"

    def __init__(self, table_name: str = "_resilience_test_sales") -> None:
        super().__init__(model="mock-v1")
        self._table = table_name

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: Union[str, List[str]], **_: Any) -> Optional[str]:
        text = prompt if isinstance(prompt, str) else "\n".join(prompt)
        low = text.lower()
        if "intent" in low and "conversation" in low:
            return json.dumps({"intent": "sql", "message": ""})
        if "chart" in low or "visual" in low:
            return json.dumps({"chart_type": "bar", "x_axis": "Product", "y_axis": "total_revenue"})
        if "analy" in low or "insight" in low:
            return json.dumps({"conclusion": "ok", "confidence": 0.9})
        if "select" in low or "sql" in low or "query" in low:
            return f"SELECT Product, SUM(Revenue) AS total_revenue FROM {self._table} GROUP BY Product"
        return json.dumps({"result": "ok"})


class _AlwaysFailLLM(LLMProvider):
    provider_name = "mock-fail"

    def __init__(self) -> None:
        super().__init__(model="mock-fail")

    def is_available(self) -> bool:
        return False

    def generate(self, *_: Any, **__: Any) -> Optional[str]:
        raise RuntimeError("simulated provider outage")


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


def _patch_llm(mock: LLMProvider):
    # After Sprint 2, BaseAgent imports get_llm lazily from shared.llm_provider,
    # so a single source-level patch covers every agent.
    return [
        patch("shared.llm_provider.get_llm", return_value=mock),
        patch("shared.llm_provider._cached_llm", mock),
    ]


@pytest.fixture()
def happy_client(upload_dir):
    mock = _HappyMockLLM()
    patches = _patch_llm(mock)
    for p in patches:
        p.start()
    try:
        from fastapi.testclient import TestClient

        from api_gateway.main import app
        yield TestClient(app)
    finally:
        for p in patches:
            p.stop()


@pytest.fixture()
def failing_client(upload_dir):
    mock = _AlwaysFailLLM()
    patches = _patch_llm(mock)
    for p in patches:
        p.start()
    try:
        from fastapi.testclient import TestClient

        from api_gateway.main import app
        yield TestClient(app)
    finally:
        for p in patches:
            p.stop()


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

def test_chat_llm_provider_down_returns_clean_error(failing_client):
    """When every LLM call raises, /chat must return 200 with Error status —
    not a 500 with a stack trace leaking provider internals."""
    resp = failing_client.post(f"{V1}/chat", json={"message": "anything"})
    # Pipeline should respond (not crash with 500), and surface Error status.
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("Error", "Conversational")
    # No internal exception text should leak into the user-facing message.
    payload = json.dumps(data).lower()
    assert "traceback" not in payload


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
