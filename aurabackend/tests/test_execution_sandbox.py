"""
Sprint S31b — Execution sandbox tests.

Tier A (pure Python, no optional deps).

Covers:
  * ExecutionJob / QueryResult schema validation
  * _infer_chart heuristic (date→line, revenue→bar, default→table)
  * execute_sql endpoint guards (unapproved, missing connection_id, empty SQL)
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import ExecutionJob, QueryResult

# ── Schema tests ──────────────────────────────────────────────────

class TestExecutionJob:
    def test_defaults(self):
        job = ExecutionJob(job_id="j1", sql="SELECT 1")
        assert job.approved is False
        assert job.status == "pending"
        assert job.connection_id is None
        assert job.limit == 1000
        assert job.result is None
        assert job.error is None

    def test_limit_bounds(self):
        with pytest.raises(Exception):
            ExecutionJob(job_id="j1", sql="SELECT 1", limit=0)
        with pytest.raises(Exception):
            ExecutionJob(job_id="j1", sql="SELECT 1", limit=10_001)

    def test_approved_job(self):
        job = ExecutionJob(
            job_id="j1", sql="SELECT * FROM sales",
            connection_id="pg1", approved=True, limit=500,
        )
        assert job.approved is True
        assert job.limit == 500


class TestQueryResult:
    def test_basic(self):
        qr = QueryResult(
            columns=["id", "name"],
            rows=[[1, "Alice"], [2, "Bob"]],
        )
        assert len(qr.rows) == 2
        assert qr.chart_spec is None

    def test_with_chart_spec(self):
        qr = QueryResult(
            columns=["month", "revenue"],
            rows=[["Jan", 100]],
            chart_spec={"type": "line"},
        )
        assert qr.chart_spec["type"] == "line"


# ── _infer_chart tests ────────────────────────────────────────────

class TestInferChart:
    def test_date_first_column_returns_line(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["date_col", "amount"])
        assert result["type"] == "line"

    def test_time_first_column_returns_line(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["created_time", "value"])
        assert result["type"] == "line"

    def test_revenue_second_column_returns_bar(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["category", "total_revenue"])
        assert result["type"] == "bar"

    def test_count_second_column_returns_bar(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["region", "count"])
        assert result["type"] == "bar"

    def test_sum_second_column_returns_bar(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["product", "sum_amount"])
        assert result["type"] == "bar"

    def test_generic_columns_returns_table(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["id", "name", "email"])
        assert result["type"] == "table"

    def test_single_column_returns_table(self):
        from execution_sandbox.main import _infer_chart
        result = _infer_chart(["value"])
        assert result["type"] == "table"


# ── execute_sql endpoint guard tests ──────────────────────────────

class TestExecuteSqlGuards:
    @pytest.fixture
    def client(self):
        from execution_sandbox.main import execution_app
        return TestClient(execution_app, raise_server_exceptions=False)

    def test_unapproved_job_rejected(self, client):
        resp = client.post("/execute_sql", json={
            "job_id": "j1", "sql": "SELECT 1",
            "connection_id": "pg1", "approved": False,
        })
        assert resp.status_code == 403
        assert "approved" in resp.json()["detail"].lower()

    def test_missing_connection_id_rejected(self, client):
        resp = client.post("/execute_sql", json={
            "job_id": "j1", "sql": "SELECT 1", "approved": True,
        })
        assert resp.status_code == 400
        assert "connection_id" in resp.json()["detail"]

    def test_empty_sql_rejected(self, client):
        resp = client.post("/execute_sql", json={
            "job_id": "j1", "sql": "   ",
            "connection_id": "pg1", "approved": True,
        })
        assert resp.status_code == 400
        assert "SQL" in resp.json()["detail"]
