"""
Sprint S31b — MCP servers + knowledge base helper tests.

Tier A (pure Python, no optional deps beyond numpy).

Covers:
  * _assert_select_only: SQL safety gate (SELECT/CTE allowed,
    INSERT/UPDATE/DELETE/DROP/multi-statement rejected)
  * _redact_dsn: credential stripping from SQLAlchemy URLs
  * knowledge_base._embed_text: SHA256 → unit-vector projection
  * knowledge_base._score_similarity: dot-product similarity
"""
from __future__ import annotations

import os
import sys

import numpy as np
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


# ── knowledge_base._embed_text tests ──────────────────────────────

class TestEmbedText:
    def test_returns_correct_dimension(self):
        from knowledge_base.main import EMBEDDING_DIM, _embed_text
        vec = _embed_text("hello world")
        assert len(vec) == EMBEDDING_DIM

    def test_unit_length(self):
        from knowledge_base.main import _embed_text
        vec = _embed_text("test string")
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 1e-5

    def test_deterministic(self):
        from knowledge_base.main import _embed_text
        v1 = _embed_text("same input")
        v2 = _embed_text("same input")
        assert v1 == v2

    def test_different_inputs_different_vectors(self):
        from knowledge_base.main import _embed_text
        v1 = _embed_text("apples")
        v2 = _embed_text("oranges")
        assert v1 != v2


# ── knowledge_base._score_similarity tests ────────────────────────

class TestScoreSimilarity:
    def test_identical_vectors(self):
        from knowledge_base.main import _score_similarity
        vec = [0.6, 0.8]
        score = _score_similarity(vec, vec)
        assert abs(score - 1.0) < 1e-5

    def test_orthogonal_vectors(self):
        from knowledge_base.main import _score_similarity
        score = _score_similarity([1.0, 0.0], [0.0, 1.0])
        assert abs(score) < 1e-5

    def test_empty_vectors(self):
        from knowledge_base.main import _score_similarity
        score = _score_similarity([], [])
        assert score == 0.0

    def test_self_similarity_of_embedded_text(self):
        from knowledge_base.main import _embed_text, _score_similarity
        vec = _embed_text("revenue analysis")
        score = _score_similarity(vec, vec)
        assert abs(score - 1.0) < 1e-5
