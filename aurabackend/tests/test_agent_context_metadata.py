"""
Sprint S33 — AgentContextMetadata typing tests.

Tier A (pure Python, no optional deps).

Validates that the TypedDict catalogues the keys actually used across
the codebase. The TypedDict itself is checked statically by type
checkers; this file pins the runtime contract that all callsites
still work and the catalogue covers every key in active use.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentContextMetadata

# ── Catalogue completeness ────────────────────────────────────────

# Every key written/read against ctx.metadata across the codebase.
# Update this when a new key is introduced.
_KNOWN_KEYS = {
    "budget_status",      # agents/base.py:execute()
    "dar_mode",           # agents/specialists/dar_research_agent.py
    "table_name",
    "profile_text",
    "question",
    "sql",
    "rows",
    "skip_planner",       # agents/langgraph_orchestrator.py
    "skip_analysis",
    "duckdb_con",
}


class TestCatalogueCompleteness:
    def test_all_known_keys_typed(self):
        annotated = set(AgentContextMetadata.__annotations__.keys())
        missing = _KNOWN_KEYS - annotated
        assert not missing, f"Keys in use but missing from TypedDict: {missing}"

    def test_no_orphan_keys(self):
        annotated = set(AgentContextMetadata.__annotations__.keys())
        orphans = annotated - _KNOWN_KEYS
        assert not orphans, (
            f"Keys typed but unused (remove or document in _KNOWN_KEYS): {orphans}"
        )

    def test_total_false(self):
        # All keys are optional — the bag accumulates state.
        assert AgentContextMetadata.__total__ is False


# ── Runtime contract ──────────────────────────────────────────────

class TestAgentContextRuntime:
    def test_default_metadata_is_empty_dict(self):
        ctx = AgentContext(user_prompt="x", task_description="y")
        assert ctx.metadata == {}

    def test_each_known_key_writable(self):
        ctx = AgentContext(user_prompt="x", task_description="y")
        ctx.metadata["budget_status"] = {"tokens_used": 100}
        ctx.metadata["dar_mode"] = "formulate"
        ctx.metadata["table_name"] = "sales"
        ctx.metadata["profile_text"] = "100 rows, 5 cols"
        ctx.metadata["question"] = "What is the trend?"
        ctx.metadata["sql"] = "SELECT * FROM sales"
        ctx.metadata["rows"] = [{"id": 1}]
        ctx.metadata["skip_planner"] = True
        ctx.metadata["skip_analysis"] = False
        ctx.metadata["duckdb_con"] = object()
        assert len(ctx.metadata) == 10

    def test_metadata_isolation_across_instances(self):
        """Two AgentContext instances must not share the default dict.
        This is what the field(default_factory=...) call protects."""
        ctx_a = AgentContext(user_prompt="a", task_description="a")
        ctx_b = AgentContext(user_prompt="b", task_description="b")
        ctx_a.metadata["budget_status"] = {"x": 1}
        assert "budget_status" not in ctx_b.metadata

    def test_unknown_key_accepted_at_runtime(self):
        # TypedDict total=False does not forbid extra keys at runtime —
        # it's only a static-check tool. Confirm callers can still
        # stash transient debug data without raising.
        ctx = AgentContext(user_prompt="x", task_description="y")
        ctx.metadata["_debug_trace"] = "ok"  # type: ignore[typeddict-unknown-key]
        assert ctx.metadata["_debug_trace"] == "ok"
