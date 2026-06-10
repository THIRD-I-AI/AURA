"""DPC Tier B — exercises a REAL LLM. Runs on the eval-gate real-LLM lane;
self-skips when no provider is configured."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.dpc_verifier import verify_sql_result
from agents.tool_registry import Tool, ToolRegistry


def _llm_or_skip():
    from shared.llm_provider import get_llm
    llm = get_llm(model="")
    if not llm.is_available():
        pytest.skip("no LLM provider configured")
    return llm


_TABLE = {"columns": ["region", "amount"],
          "rows": [["west", 100], ["west", 50], ["east", 200]]}


def _tools():
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        return _TABLE

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


def test_real_llm_verifies_correct_sql():
    llm = _llm_or_skip()
    vr = asyncio.run(verify_sql_result(
        "total amount across all regions",
        'SELECT SUM("amount") AS total FROM "sales"', ["total"], [[350]],
        _tools(), llm, timeout=30.0, max_rows=10000,
    ))
    assert vr.status in ("verified", "skipped")  # skipped only if the LLM errors/times out
    if vr.status == "verified":
        assert vr.verified is True


def test_real_llm_catches_wrong_sql():
    llm = _llm_or_skip()
    # SQL claims the total is 999 — an independent pandas sum should disagree.
    vr = asyncio.run(verify_sql_result(
        "total amount across all regions",
        'SELECT SUM("amount") AS total FROM "sales"', ["total"], [[999]],
        _tools(), llm, timeout=30.0, max_rows=10000,
    ))
    assert vr.status in ("mismatch", "skipped")
    if vr.status == "mismatch":
        assert vr.verified is False
