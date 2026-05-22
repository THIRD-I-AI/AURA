"""
SQL Safety Layer for AURA
Query validation, linting, and guardrails
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class QueryRiskLevel(Enum):
    """Query risk classification"""
    SAFE = "safe"
    LOW_RISK = "low_risk"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"
    CRITICAL = "critical"


@dataclass
class ValidationResult:
    """Result of query validation"""
    is_valid: bool
    risk_level: QueryRiskLevel
    warnings: List[str]
    errors: List[str]
    suggested_query: Optional[str] = None
    row_count_estimate: int = 1000


class SQLSafetyValidator:
    """Validates and sanitizes SQL queries"""

    # Forbidden operations
    FORBIDDEN_KEYWORDS = {
        "DROP": "destructive operation",
        "DELETE": "destructive operation",
        "TRUNCATE": "truncates data",
        "ALTER": "modifies schema",
        "CREATE": "creates new objects",
        "INSERT": "modifies data",
        "UPDATE": "modifies data",
        "EXEC": "executes stored procedures",
        "EXECUTE": "executes stored procedures",
    }

    # Suspicious patterns
    SUSPICIOUS_PATTERNS = {
        r";\s*DROP": "command injection attempt",
        r"--\s*(DROP|DELETE|TRUNCATE)": "comment-based injection",
        r"/\*.*DROP": "comment-based injection",
        r"UNION.*SELECT": "potential data exfiltration",
        r"xp_": "extended stored procedure access",
        r"sp_": "system stored procedure access",
    }

    # Performance red flags
    PERFORMANCE_WARNINGS = {
        r"SELECT\s+\*": "inefficient: select specific columns",
        r"WHERE.*LIKE\s+'%": "performance: leading wildcard",
        r"JOIN.*JOIN.*JOIN": "complex joins: may be slow",
        r"ORDER BY\s+\d+": "order by ordinal (brittle)",
    }

    # Cost estimators (relative costs for different operations)
    OPERATION_COSTS = {
        "SELECT": 1,
        "GROUP BY": 3,
        "JOIN": 2,
        "DISTINCT": 2,
        "ORDER BY": 2,
        "SUBQUERY": 4,
    }

    def __init__(self, max_rows: int = 10000, dry_run_only: bool = False):
        """
        Initialize SQL safety validator

        Args:
            max_rows: Maximum rows to allow in result
            dry_run_only: Only allow SELECT queries (dry run mode)
        """
        self.max_rows = max_rows
        self.dry_run_only = dry_run_only

    def validate(self, query: str) -> ValidationResult:
        """Validate SQL query for safety"""
        warnings = []
        errors = []
        risk_level = QueryRiskLevel.SAFE
        suggested_query = None

        # Normalize query
        query_upper = query.strip().upper()

        # Check for forbidden operations
        if self.dry_run_only:
            # In dry-run mode, only SELECT allowed
            if not query_upper.startswith("SELECT"):
                errors.append("Dry-run mode: only SELECT queries allowed")
                risk_level = QueryRiskLevel.CRITICAL
        else:
            # Check for destructive operations
            for keyword, reason in self.FORBIDDEN_KEYWORDS.items():
                if re.search(rf"\b{keyword}\b", query_upper):
                    errors.append(f"Forbidden operation: {keyword} ({reason})")
                    risk_level = QueryRiskLevel.CRITICAL

        # Check for suspicious patterns
        for pattern, reason in self.SUSPICIOUS_PATTERNS.items():
            if re.search(pattern, query_upper, re.IGNORECASE):
                errors.append(f"Security risk: {reason}")
                risk_level = QueryRiskLevel.CRITICAL

        # Check for performance issues via AST (finding #4)
        for warning in self._check_performance_ast(query):
            warnings.append(warning)
            if risk_level in (QueryRiskLevel.SAFE, QueryRiskLevel.LOW_RISK):
                risk_level = QueryRiskLevel.LOW_RISK

        # Extract LIMIT clause
        limit_match = re.search(r"LIMIT\s+(\d+)", query_upper)
        row_count_estimate = self.max_rows

        if limit_match:
            limit_val = int(limit_match.group(1))
            row_count_estimate = min(limit_val, self.max_rows)
        else:
            # No LIMIT found - warning and suggest addition
            warnings.append(f"No LIMIT clause: results will be truncated to {self.max_rows}")
            if risk_level == QueryRiskLevel.SAFE:
                risk_level = QueryRiskLevel.LOW_RISK
            # Suggest adding LIMIT
            suggested_query = f"{query.rstrip().rstrip(';')} LIMIT {self.max_rows};"

        # Check query complexity/estimated cost
        cost = self._estimate_query_cost(query_upper)
        if cost > 10:
            warnings.append(f"Complex query detected (cost estimate: {cost}): execution may take longer")
            if risk_level == QueryRiskLevel.SAFE:
                risk_level = QueryRiskLevel.LOW_RISK

        # Check result set size risk
        if row_count_estimate > self.max_rows:
            warnings.append("Large result set: may exceed memory limits")
            if risk_level in (QueryRiskLevel.SAFE, QueryRiskLevel.LOW_RISK):
                risk_level = QueryRiskLevel.MEDIUM_RISK

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            risk_level=risk_level,
            warnings=warnings,
            errors=errors,
            suggested_query=suggested_query,
            row_count_estimate=row_count_estimate,
        )

    def _estimate_query_cost(self, query: str) -> int:
        """Estimate relative query cost via sqlglot AST node counts."""
        try:
            import sqlglot
            from sqlglot import exp

            parsed = sqlglot.parse(query, error_level="ignore")
            if not parsed or parsed[0] is None:
                return 0
            stmt = parsed[0]
            cost = 0
            cost += len(list(stmt.find_all(exp.Join))) * self.OPERATION_COSTS["JOIN"]
            cost += (1 if stmt.find(exp.Group) else 0) * self.OPERATION_COSTS["GROUP BY"]
            cost += (1 if stmt.find(exp.Distinct) else 0) * self.OPERATION_COSTS["DISTINCT"]
            cost += (1 if stmt.find(exp.Order) else 0) * self.OPERATION_COSTS["ORDER BY"]
            cost += len(list(stmt.find_all(exp.Subquery))) * self.OPERATION_COSTS["SUBQUERY"]
            return cost
        except Exception:
            cost = 0
            for op, op_cost in self.OPERATION_COSTS.items():
                if f" {op}" in query or f" {op} " in query:
                    cost += op_cost
            return cost

    def _check_performance_ast(self, query: str) -> List[str]:
        """Detect performance anti-patterns via sqlglot AST (finding #4)."""
        try:
            import sqlglot
            from sqlglot import exp

            parsed = sqlglot.parse(query, error_level="ignore")
            if not parsed or parsed[0] is None:
                return self._check_performance_regex(query)
            stmt = parsed[0]
            found: List[str] = []

            if stmt.find(exp.Star):
                found.append("inefficient: select specific columns")

            for like in stmt.find_all(exp.Like):
                rhs = like.args.get("expression")
                if isinstance(rhs, exp.Literal) and str(rhs.this).startswith("%"):
                    found.append("performance: leading wildcard")
                    break

            if len(list(stmt.find_all(exp.Join))) >= 3:
                found.append("complex joins: may be slow")

            for ordered in stmt.find_all(exp.Ordered):
                if isinstance(ordered.this, exp.Literal) and not ordered.this.is_string:
                    found.append("order by ordinal (brittle)")
                    break

            return found
        except Exception:
            return self._check_performance_regex(query)

    def _check_performance_regex(self, query: str) -> List[str]:
        """Regex fallback for environments where sqlglot is unavailable."""
        found = []
        for pattern, warning in self.PERFORMANCE_WARNINGS.items():
            if re.search(pattern, query.upper(), re.IGNORECASE):
                found.append(warning)
        return found

    def add_safety_limit(self, query: str) -> str:
        """Add LIMIT clause if missing"""
        if "LIMIT" in query.upper():
            return query
        return f"{query.rstrip().rstrip(';')} LIMIT {self.max_rows};"

    def dry_run_mode(self, query: str) -> str:
        """Convert query to dry-run (EXPLAIN or LIMIT 0)"""
        # For SELECT, add LIMIT 0 to see structure
        if query.strip().upper().startswith("SELECT"):
            return f"SELECT * FROM ({query}) AS dry_run LIMIT 0"
        # For other queries, prefix with EXPLAIN
        return f"EXPLAIN {query}"

    def lint_query(self, query: str) -> List[str]:
        """Lint query for style and optimization suggestions"""
        suggestions = []
        query_upper = query.upper()

        # Style checks
        if query != query.strip():
            suggestions.append("Extra whitespace in query")

        if "\n" in query and not query_upper.count("\n") >= 2:
            suggestions.append("Consider formatting multi-line for readability")

        # Optimization checks
        if "SELECT *" in query_upper:
            suggestions.append("Use specific column names instead of SELECT *")

        if "WHERE 1=1" in query_upper:
            suggestions.append("Remove WHERE 1=1 clause")

        if "DISTINCT" in query_upper and "GROUP BY" in query_upper:
            suggestions.append("Using both DISTINCT and GROUP BY is often redundant")

        return suggestions


class QueryPlanner:
    """Analyzes and plans query execution"""

    @staticmethod
    def estimate_execution_time(query: str, row_count: int = 1000000) -> Tuple[float, str]:
        """
        Estimate query execution time (rough approximation).

        Uses sqlglot AST to count joins linearly — the old string-count
        approach used 2**join_count which blew up exponentially.

        Returns:
            Tuple of (estimated_time_ms, explanation)
        """
        base_time = 0.1
        explanation: List[str] = []

        try:
            import sqlglot
            from sqlglot import exp

            parsed = sqlglot.parse(query, error_level="ignore")
            stmt = (parsed or [None])[0]
        except Exception:
            stmt = None

        if stmt is None:
            query_upper = query.upper()
            base_time += row_count * 0.001
            explanation.append("Full table scan with filter" if "WHERE" in query_upper else "Full table scan")
            join_count = query_upper.count("JOIN")
            if join_count:
                base_time *= (1 + 0.5 * join_count)
                explanation.append(f"Includes {join_count} join(s)")
            if "GROUP BY" in query_upper:
                base_time *= 3
                explanation.append("Includes grouping")
            if "ORDER BY" in query_upper:
                base_time *= 2
                explanation.append("Includes sorting")
            if "DISTINCT" in query_upper:
                base_time *= 1.5
                explanation.append("Includes DISTINCT")
            return (base_time, "; ".join(explanation))

        base_time += row_count * 0.001
        has_where = stmt.find(exp.Where) is not None
        explanation.append("Full table scan with filter" if has_where else "Full table scan")

        join_count = len(list(stmt.find_all(exp.Join)))
        if join_count:
            base_time *= (1 + 0.5 * join_count)
            explanation.append(f"Includes {join_count} join(s)")

        if stmt.find(exp.Group):
            base_time *= 3
            explanation.append("Includes grouping")

        if stmt.find(exp.Order):
            base_time *= 2
            explanation.append("Includes sorting")

        if stmt.find(exp.Distinct):
            base_time *= 1.5
            explanation.append("Includes DISTINCT")

        return (base_time, "; ".join(explanation))

    @staticmethod
    def get_cost_estimate(query: str) -> Dict[str, Any]:
        """Get detailed cost estimate"""
        return {
            "memory_estimate_mb": 100,
            "cpu_estimate_percent": 25,
            "io_estimate_operations": 1000,
            "estimated_cost_usd": 0.001,
            "category": "low-cost",
        }
