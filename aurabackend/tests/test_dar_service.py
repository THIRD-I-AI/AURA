"""
Sprint S31a — DAR service tests.

Tier A (pure Python, no optional deps).

Covers:
  * Pydantic schema validation (ColumnProfile, DARState, Finding, etc.)
  * Helper functions (_err, _completed)
  * DARState.succeeded property
  * Node functions with mocked DuckDB connections
"""
from __future__ import annotations

import math
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dar_service.schemas import (
    ColumnProfile,
    DARState,
    Finding,
    NodeError,
    QueryResult,
    ResearchQuestion,
)

# ── Schema tests ────────────────────────────────────────────────────

class TestColumnProfile:
    def test_minimal(self):
        cp = ColumnProfile(column="age", data_type="INTEGER")
        assert cp.column == "age"
        assert cp.null_rate == 0.0
        assert cp.top_values == []

    def test_full(self):
        cp = ColumnProfile(
            column="revenue",
            data_type="DOUBLE",
            null_rate=0.05,
            distinct_count=42,
            mean=100.5,
            std=12.3,
            min=0.0,
            max=999.9,
            top_values=[{"value": 100, "count": 5}],
        )
        assert cp.distinct_count == 42
        assert cp.mean == 100.5

    def test_extra_field_rejected(self):
        with pytest.raises(Exception):
            ColumnProfile(column="x", data_type="INT", bogus="nope")


class TestResearchQuestion:
    def test_valid(self):
        rq = ResearchQuestion(question="What is the trend?", sql="SELECT 1")
        assert rq.question == "What is the trend?"

    def test_empty_question_rejected(self):
        with pytest.raises(Exception):
            ResearchQuestion(question="", sql="SELECT 1")

    def test_empty_sql_rejected(self):
        with pytest.raises(Exception):
            ResearchQuestion(question="Valid?", sql="")


class TestQueryResult:
    def test_defaults(self):
        qr = QueryResult(question="q", sql="SELECT 1")
        assert qr.columns == []
        assert qr.rows == []
        assert qr.row_count == 0
        assert qr.error is None

    def test_with_data(self):
        qr = QueryResult(
            question="q", sql="SELECT 1",
            columns=["a", "b"], rows=[[1, 2]], row_count=1,
        )
        assert qr.row_count == 1


class TestFinding:
    def test_valid_types(self):
        for ft in ("anomaly", "trend", "correlation", "summary"):
            f = Finding(question="q", sql="s", finding_type=ft, summary="ok", score=0.5)
            assert f.finding_type == ft

    def test_score_bounds(self):
        with pytest.raises(Exception):
            Finding(question="q", sql="s", finding_type="trend", summary="x", score=1.5)
        with pytest.raises(Exception):
            Finding(question="q", sql="s", finding_type="trend", summary="x", score=-0.1)

    def test_is_anomaly_default(self):
        f = Finding(question="q", sql="s", finding_type="anomaly", summary="x", score=0.9)
        assert f.is_anomaly is False


class TestNodeError:
    def test_basic(self):
        ne = NodeError(node="introspect", message="boom")
        assert ne.duration_ms == 0.0


class TestDARState:
    def _base(self, **kw):
        defaults = dict(run_id="r1", source_id="s1", table_name="t1", duckdb_path="/tmp/test.db")
        defaults.update(kw)
        return DARState(**defaults)

    def test_succeeded_when_no_errors(self):
        s = self._base()
        assert s.succeeded is True

    def test_succeeded_false_with_errors(self):
        s = self._base(errors=[NodeError(node="x", message="fail")])
        assert s.succeeded is False

    def test_defaults(self):
        s = self._base()
        assert s.schema_columns == []
        assert s.profile_text == ""
        assert s.questions == []
        assert s.completed_nodes == []


# ── Helper function tests ───────────────────────────────────────────

class TestHelpers:
    def test_err_appends_error(self):
        from dar_service.graph import _err
        state = DARState(run_id="r", source_id="s", table_name="t", duckdb_path="/x")
        result = _err(state, "node1", "something broke", 42.0)
        assert len(result["errors"]) == 1
        assert result["errors"][0].node == "node1"
        assert result["errors"][0].message == "something broke"
        assert result["errors"][0].duration_ms == 42.0

    def test_err_preserves_existing_errors(self):
        from dar_service.graph import _err
        existing = NodeError(node="prev", message="old")
        state = DARState(
            run_id="r", source_id="s", table_name="t", duckdb_path="/x",
            errors=[existing],
        )
        result = _err(state, "new", "new error")
        assert len(result["errors"]) == 2
        assert result["errors"][0].node == "prev"
        assert result["errors"][1].node == "new"

    def test_completed_appends_node(self):
        from dar_service.graph import _completed
        state = DARState(
            run_id="r", source_id="s", table_name="t", duckdb_path="/x",
            completed_nodes=["a"],
        )
        result = _completed(state, "b")
        assert result == ["a", "b"]


# ── Node function tests (mocked DuckDB) ────────────────────────────

class TestIntrospectNode:
    def _state(self):
        return DARState(run_id="r", source_id="s", table_name="sales", duckdb_path="/fake.db")

    @patch("dar_service.graph._open_duckdb")
    def test_success(self, mock_open):
        from dar_service.graph import introspect_node
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchall.return_value = [
            ("id", "INTEGER", "NO", None, None, None),
            ("name", "VARCHAR", "YES", None, None, None),
        ]
        mock_open.return_value = mock_con

        result = introspect_node(self._state())
        assert "schema_columns" in result
        assert len(result["schema_columns"]) == 2
        assert result["schema_columns"][0].column == "id"
        assert "introspect" in result["completed_nodes"]
        mock_con.close.assert_called_once()

    @patch("dar_service.graph._open_duckdb")
    def test_empty_table_returns_error(self, mock_open):
        from dar_service.graph import introspect_node
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchall.return_value = []
        mock_open.return_value = mock_con

        result = introspect_node(self._state())
        assert "errors" in result
        assert result["errors"][-1].node == "introspect"
        assert "no columns" in result["errors"][-1].message

    @patch("dar_service.graph._open_duckdb", side_effect=Exception("connection refused"))
    def test_duckdb_connection_failure(self, mock_open):
        from dar_service.graph import introspect_node
        result = introspect_node(self._state())
        assert "errors" in result
        assert "connection refused" in result["errors"][-1].message


class TestExecuteNode:
    def _state(self):
        return DARState(
            run_id="r", source_id="s", table_name="sales", duckdb_path="/fake.db",
            questions=[
                ResearchQuestion(question="Total sales?", sql="SELECT SUM(amount) FROM sales"),
                ResearchQuestion(question="Bad query", sql="INVALID SQL"),
            ],
        )

    @patch("dar_service.graph._open_duckdb")
    def test_mixed_success_and_failure(self, mock_open):
        from dar_service.graph import execute_node
        mock_con = MagicMock()

        call_count = [0]
        def side_effect(sql):
            call_count[0] += 1
            if call_count[0] == 1:
                result = MagicMock()
                result.description = [("sum_amount",)]
                result.fetchall.return_value = [(42000,)]
                return result
            else:
                raise Exception("syntax error")

        mock_con.execute = side_effect
        mock_open.return_value = mock_con

        result = execute_node(self._state())
        assert len(result["query_results"]) == 2
        assert result["query_results"][0].row_count == 1
        assert result["query_results"][0].columns == ["sum_amount"]
        assert result["query_results"][1].error is not None
        assert "syntax error" in result["query_results"][1].error

    @patch("dar_service.graph._open_duckdb", side_effect=Exception("db locked"))
    def test_connection_failure(self, mock_open):
        from dar_service.graph import execute_node
        result = execute_node(self._state())
        assert "errors" in result
        assert "db locked" in result["errors"][-1].message


class TestScoreNode:
    def _state(self):
        return DARState(
            run_id="r", source_id="s", table_name="sales", duckdb_path="/fake.db",
            query_results=[
                QueryResult(question="q1", sql="s1", error="timeout", row_count=0),
                QueryResult(
                    question="q2", sql="s2",
                    columns=["a"], rows=[[1]], row_count=1,
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_error_result_gets_low_score(self):
        from dar_service.graph import score_node
        state = DARState(
            run_id="r", source_id="s", table_name="sales", duckdb_path="/fake.db",
            query_results=[
                QueryResult(question="q", sql="s", error="timeout", row_count=0),
            ],
        )
        with patch("dar_service.graph._agent") as mock_agent_fn:
            result = await score_node(state)

        assert len(result["findings"]) == 1
        assert result["findings"][0].score == 0.05
        assert result["findings"][0].finding_type == "summary"
        mock_agent_fn.assert_not_called()
