"""
SQL Safety Validator & Query Planner Unit Tests
=================================================
Tests for SQLSafetyValidator (risk classification, forbidden keywords,
injection detection, LIMIT handling, dry-run mode, linting) and
QueryPlanner (cost estimation).
"""

import pytest

from safety.validator import QueryPlanner, QueryRiskLevel, SQLSafetyValidator

# ── Validator basics ─────────────────────────────────────────────────────────

class TestValidatorBasics:
    def test_safe_select_with_limit(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id, name FROM users LIMIT 10")
        assert result.is_valid
        assert result.risk_level == QueryRiskLevel.SAFE

    def test_select_star_is_low_risk(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT * FROM users LIMIT 10")
        assert result.is_valid
        assert result.risk_level == QueryRiskLevel.LOW_RISK

    def test_no_limit_adds_warning(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id FROM users")
        assert result.is_valid
        assert any("LIMIT" in w for w in result.warnings)
        assert result.suggested_query is not None
        assert "LIMIT" in result.suggested_query

    def test_suggested_query_strips_trailing_semicolons(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id FROM users;")
        assert result.suggested_query.count(";") == 1


# ── Forbidden operations ────────────────────────────────────────────────────

class TestForbiddenOperations:
    @pytest.mark.parametrize("query", [
        "DROP TABLE users",
        "DELETE FROM sales WHERE 1=1",
        "TRUNCATE TABLE logs",
        "ALTER TABLE users ADD COLUMN hack TEXT",
        "INSERT INTO users VALUES (1, 'evil')",
        "UPDATE users SET admin=true",
    ])
    def test_destructive_queries_blocked(self, query):
        v = SQLSafetyValidator()
        result = v.validate(query)
        assert not result.is_valid
        assert result.risk_level == QueryRiskLevel.CRITICAL
        assert len(result.errors) > 0

    def test_select_is_not_forbidden(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT COUNT(*) FROM users LIMIT 1")
        assert result.is_valid


# ── Injection detection ─────────────────────────────────────────────────────

class TestInjectionDetection:
    def test_semicolon_drop_injection(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT 1; DROP TABLE users")
        assert not result.is_valid
        assert result.risk_level == QueryRiskLevel.CRITICAL

    def test_union_select_injection(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id FROM users UNION SELECT password FROM credentials")
        assert not result.is_valid

    def test_comment_injection(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT 1 -- DROP TABLE users")
        assert not result.is_valid


# ── Dry-run mode ────────────────────────────────────────────────────────────

class TestDryRunMode:
    def test_select_allowed(self):
        v = SQLSafetyValidator(dry_run_only=True)
        result = v.validate("SELECT * FROM sales LIMIT 10")
        assert result.is_valid

    def test_insert_blocked(self):
        v = SQLSafetyValidator(dry_run_only=True)
        result = v.validate("INSERT INTO sales VALUES (1, 'test', 100)")
        assert not result.is_valid
        assert result.risk_level == QueryRiskLevel.CRITICAL

    def test_update_blocked(self):
        v = SQLSafetyValidator(dry_run_only=True)
        result = v.validate("UPDATE users SET role='admin'")
        assert not result.is_valid

    def test_dry_run_conversion_select(self):
        v = SQLSafetyValidator()
        dry = v.dry_run_mode("SELECT id FROM users")
        assert "LIMIT 0" in dry

    def test_dry_run_conversion_non_select(self):
        v = SQLSafetyValidator()
        dry = v.dry_run_mode("CREATE TABLE foo (id INT)")
        assert dry.startswith("EXPLAIN")


# ── Performance warnings ────────────────────────────────────────────────────

class TestPerformanceWarnings:
    def test_leading_wildcard_warning(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id FROM users WHERE name LIKE '%test' LIMIT 10")
        assert any("wildcard" in w.lower() for w in result.warnings)

    def test_multiple_joins_warning(self):
        v = SQLSafetyValidator()
        result = v.validate(
            "SELECT a.id FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id "
            "JOIN d ON c.id=d.id LIMIT 10"
        )
        assert any("join" in w.lower() for w in result.warnings)

    def test_ordinal_order_by_warning(self):
        v = SQLSafetyValidator()
        result = v.validate("SELECT id, name FROM users ORDER BY 1 LIMIT 10")
        assert any("ordinal" in w.lower() for w in result.warnings)


# ── Lint ─────────────────────────────────────────────────────────────────────

class TestLint:
    def test_select_star_lint(self):
        v = SQLSafetyValidator()
        suggestions = v.lint_query("SELECT * FROM users")
        assert any("column" in s.lower() for s in suggestions)

    def test_where_1_1_lint(self):
        v = SQLSafetyValidator()
        suggestions = v.lint_query("SELECT id FROM users WHERE 1=1")
        assert any("1=1" in s for s in suggestions)

    def test_distinct_group_by_lint(self):
        v = SQLSafetyValidator()
        suggestions = v.lint_query("SELECT DISTINCT name FROM users GROUP BY name")
        assert any("redundant" in s.lower() for s in suggestions)

    def test_clean_query_no_suggestions(self):
        v = SQLSafetyValidator()
        suggestions = v.lint_query("SELECT id, name FROM users WHERE active = true")
        # Only whitespace-related or none
        assert len(suggestions) <= 1


# ── Safety limit ─────────────────────────────────────────────────────────────

class TestSafetyLimit:
    def test_adds_limit_when_missing(self):
        v = SQLSafetyValidator(max_rows=500)
        q = v.add_safety_limit("SELECT id FROM users")
        assert "LIMIT 500" in q

    def test_preserves_existing_limit(self):
        v = SQLSafetyValidator(max_rows=500)
        q = v.add_safety_limit("SELECT id FROM users LIMIT 10")
        assert q == "SELECT id FROM users LIMIT 10"

    def test_custom_max_rows(self):
        v = SQLSafetyValidator(max_rows=50)
        result = v.validate("SELECT id FROM users")
        assert "50" in result.suggested_query


# ── Query cost estimation ────────────────────────────────────────────────────

class TestQueryCost:
    def test_simple_select_low_cost(self):
        v = SQLSafetyValidator()
        cost = v._estimate_query_cost("SELECT id FROM users")
        assert cost >= 0  # Simple select with no aggregation

    def test_join_increases_cost(self):
        v = SQLSafetyValidator()
        simple = v._estimate_query_cost("SELECT id FROM users")
        joined = v._estimate_query_cost("SELECT id FROM users JOIN orders ON users.id = orders.user_id")
        assert joined > simple

    def test_group_by_increases_cost(self):
        v = SQLSafetyValidator()
        simple = v._estimate_query_cost("SELECT id FROM users")
        grouped = v._estimate_query_cost("SELECT id FROM users GROUP BY id")
        assert grouped > simple


# ── QueryPlanner ─────────────────────────────────────────────────────────────

class TestQueryPlanner:
    def test_simple_query_time(self):
        time_ms, explanation = QueryPlanner.estimate_execution_time(
            "SELECT * FROM sales LIMIT 100"
        )
        assert time_ms > 0
        assert "Full table scan" in explanation

    def test_join_increases_time(self):
        t1, _ = QueryPlanner.estimate_execution_time("SELECT * FROM sales LIMIT 100")
        t2, e2 = QueryPlanner.estimate_execution_time(
            "SELECT * FROM sales JOIN customers ON sales.cid = customers.id"
        )
        assert t2 > t1
        assert "join" in e2.lower()

    def test_group_by_increases_time(self):
        t1, _ = QueryPlanner.estimate_execution_time("SELECT * FROM sales")
        t2, e2 = QueryPlanner.estimate_execution_time(
            "SELECT product, SUM(revenue) FROM sales GROUP BY product"
        )
        assert t2 > t1
        assert "grouping" in e2.lower()

    def test_order_by_increases_time(self):
        t1, _ = QueryPlanner.estimate_execution_time("SELECT * FROM sales")
        t2, e2 = QueryPlanner.estimate_execution_time(
            "SELECT * FROM sales ORDER BY revenue"
        )
        assert t2 > t1
        assert "sorting" in e2.lower()

    def test_cost_estimate_structure(self):
        cost = QueryPlanner.get_cost_estimate("SELECT 1")
        assert "memory_estimate_mb" in cost
        assert "category" in cost
        assert cost["category"] == "low-cost"

    def test_join_multiplier_is_linear_not_exponential(self):
        # Old code: 2**3 = 8x. New code: (1 + 0.5*3) = 2.5x.
        t1, _ = QueryPlanner.estimate_execution_time("SELECT * FROM a")
        t3, _ = QueryPlanner.estimate_execution_time(
            "SELECT * FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id JOIN d ON c.id=d.id"
        )
        # 8x multiplier would give t3 > t1 * 7; linear gives t3 < t1 * 5.
        assert t3 < t1 * 5


# ── P-3 AST-based cost and performance checks ────────────────────────────────

class TestASTCost:
    def test_multiple_joins_cost(self):
        v = SQLSafetyValidator()
        q = "SELECT a.id FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id JOIN d ON c.id=d.id"
        cost = v._estimate_query_cost(q)
        assert cost >= 6  # 3 joins × 2

    def test_subquery_cost(self):
        v = SQLSafetyValidator()
        q = "SELECT id FROM (SELECT id FROM users WHERE active=1) AS sub LIMIT 10"
        cost = v._estimate_query_cost(q)
        assert cost >= 4  # one subquery × 4

    def test_group_by_cost(self):
        v = SQLSafetyValidator()
        q = "SELECT dept, COUNT(*) FROM employees GROUP BY dept"
        cost = v._estimate_query_cost(q)
        assert cost >= 3  # GROUP BY × 3


class TestASTPerformanceWarnings:
    def test_select_star_warning(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast("SELECT * FROM users")
        assert any("select specific" in w.lower() for w in found)

    def test_leading_wildcard_warns(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast("SELECT id FROM users WHERE name LIKE '%test'")
        assert any("wildcard" in w.lower() for w in found)

    def test_trailing_wildcard_no_warning(self):
        # Trailing wildcard is index-friendly — must not warn.
        v = SQLSafetyValidator()
        found = v._check_performance_ast("SELECT id FROM users WHERE name LIKE 'test%'")
        assert not any("wildcard" in w.lower() for w in found)

    def test_three_joins_warns(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast(
            "SELECT a.id FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id JOIN d ON c.id=d.id"
        )
        assert any("join" in w.lower() for w in found)

    def test_two_joins_no_warning(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast(
            "SELECT a.id FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id"
        )
        assert not any("join" in w.lower() for w in found)

    def test_ordinal_order_by_warns(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast("SELECT id, name FROM users ORDER BY 1")
        assert any("ordinal" in w.lower() for w in found)

    def test_named_order_by_no_warning(self):
        v = SQLSafetyValidator()
        found = v._check_performance_ast("SELECT id, name FROM users ORDER BY name")
        assert not any("ordinal" in w.lower() for w in found)
