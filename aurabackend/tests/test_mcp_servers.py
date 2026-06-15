"""
Sprint S31b — MCP server helper tests.

Tier A (pure Python).

Covers:
  * _assert_select_only: SQL safety gate (SELECT/CTE allowed,
    INSERT/UPDATE/DELETE/DROP/multi-statement rejected)
  * _redact_dsn: credential stripping from SQLAlchemy URLs
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.aura_mcp_server import _assert_select_only, _redact_dsn

# ── _assert_select_only tests ─────────────────────────────────────

class TestAssertSelectOnly:
    def test_simple_select(self):
        _assert_select_only("SELECT 1")

    def test_select_from_table(self):
        _assert_select_only("SELECT * FROM sales WHERE amount > 100")

    def test_cte_allowed(self):
        _assert_select_only(
            "WITH top AS (SELECT * FROM sales LIMIT 10) SELECT * FROM top"
        )

    def test_select_with_subquery(self):
        _assert_select_only(
            "SELECT * FROM (SELECT id, name FROM users) AS sub"
        )

    def test_insert_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("INSERT INTO sales (id) VALUES (1)")

    def test_update_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("UPDATE sales SET amount = 0")

    def test_delete_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("DELETE FROM sales")

    def test_drop_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("DROP TABLE sales")

    def test_create_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("CREATE TABLE evil (id INT)")

    def test_multi_statement_rejected(self):
        with pytest.raises(ValueError):
            _assert_select_only("SELECT 1; DROP TABLE sales")

    def test_empty_rejected(self):
        with pytest.raises((ValueError, IndexError)):
            _assert_select_only("")


# ── _redact_dsn tests ─────────────────────────────────────────────

class TestRedactDSN:
    def test_postgres_password_redacted(self):
        dsn = "postgresql+asyncpg://user:secret@host:5432/db"
        result = _redact_dsn(dsn)
        assert "secret" not in result
        assert "***" in result
        assert "user" in result
        assert "host" in result

    def test_sqlite_no_password(self):
        dsn = "sqlite+aiosqlite:///data/metadata.db"
        result = _redact_dsn(dsn)
        assert "metadata.db" in result

    def test_invalid_dsn_returns_opaque(self):
        result = _redact_dsn("not-a-real-dsn-at-all")
        assert result in ("<opaque>", "not-a-real-dsn-at-all")
