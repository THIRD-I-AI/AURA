"""Audit round 10, safety-evolution area: regression tests for BUG-532..535."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.models import AgentFeedback, ExecutionPattern, PatternType
from evolution.pattern_library import PatternLibrary, _intent_hash
from safety.validator import SQLSafetyValidator


@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from metadata_store.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


# ── BUG-532: valid read-only queries were rejected ──────────────────

@pytest.mark.parametrize("sql", [
    "SELECT card_id, exp_date FROM cards LIMIT 10",
    "SELECT avg(resp_time) FROM requests LIMIT 10",
    "SELECT count(*) FROM audit WHERE action = 'update' LIMIT 10",
    "SELECT count(*) FROM audit WHERE action IN ('delete', 'it''s drop') LIMIT 10",
    "SELECT xp_count, sp_rate, hosp_id FROM stats LIMIT 10",
    'SELECT "update", "delete" FROM changelog LIMIT 10',
    "SELECT update_count, deleted_at FROM t WHERE note = 'drop it; insert later' LIMIT 10",
])
def test_bug_532_read_only_queries_are_valid(sql):
    for validator in (SQLSafetyValidator(), SQLSafetyValidator(dry_run_only=True)):
        result = validator.validate(sql)
        assert result.is_valid, result.errors


@pytest.mark.parametrize("sql", [
    "SELECT 'a'; DELETE FROM users",
    "SELECT 'x' FROM t; UPDATE users SET role = 'admin'",
    # A keyword placed between two literals must stay visible.
    "SELECT 'a;b' DELETE 'c'",
    # Under backslash-escaping dialects (MySQL, E'') the DELETE below is code, not
    # literal text, so literal stripping must not hide it.
    "SELECT '\\' 'x DELETE x' '",
    # Inside a block comment the quotes do not open a string.
    "SELECT 1 /* ' */ DELETE FROM t /* ' */",
    "EXEC sp_executesql N'select 1'",
    "SELECT * FROM t; EXEC xp_cmdshell 'dir'",
    # A bare procedure name at the start of a statement is a T-SQL call.
    "xp_cmdshell 'dir'",
    "SELECT 1; master.dbo.xp_cmdshell 'dir'",
    "sp_configure 'show advanced options', 1",
    "SELECT 1\nGO\nxp_cmdshell 'dir'",
    "DELETE FROM users",
    "UPDATE users SET role = 'admin'",
    # MySQL '#' comment: the quote after it does not open a string there.
    "SELECT 1 # '\nDELETE FROM t",
])
def test_bug_532_destructive_statements_still_blocked(sql):
    assert not SQLSafetyValidator().validate(sql).is_valid
    assert not SQLSafetyValidator(dry_run_only=True).validate(sql).is_valid


# ── BUG-533: dry-run mode skipped the forbidden-keyword scan ────────

@pytest.mark.parametrize("sql", [
    "SELECT 1; DELETE FROM users",
    "SELECT 1; UPDATE users SET role = 'admin'",
    "SELECT 1; INSERT INTO users VALUES (1)",
    "SELECT 1; ALTER TABLE users ADD COLUMN x INT",
])
def test_bug_533_dry_run_still_blocks_stacked_destructive_statement(sql):
    assert not SQLSafetyValidator().validate(sql).is_valid
    assert not SQLSafetyValidator(dry_run_only=True).validate(sql).is_valid


# ── BUG-534: feedback stored full query results, unbounded ──────────

async def test_bug_534_feedback_does_not_store_query_result_rows(monkeypatch, db_session):
    from sqlalchemy import select

    from evolution.engine import EvolutionEngine

    async def _fake_get_session():
        yield db_session

    monkeypatch.setattr("evolution.engine.get_session", _fake_get_session)

    rows = [[i, f"customer-{i}@example.com", i * 1.5] for i in range(10_000)]
    output = {
        "records": [{"id": r[0], "email": r[1], "amount": r[2]} for r in rows],
        "columns": ["id", "email", "amount"],
        "rows": rows,
        "sql": "SELECT id, email, amount FROM customers",
        "truncated": False,
    }
    await EvolutionEngine().record_feedback(
        session_id="plan-1", agent_name="ExecutionAgent", task_type="sql",
        user_prompt="list customers", agent_output=output, success=True, duration_ms=5.0,
    )

    stored = (await db_session.execute(select(AgentFeedback))).scalars().one().agent_output
    text = json.dumps(stored)
    # Plain bools: pytest's diff of a multi-MB string would take minutes.
    leaked_rows = "customer-0@example.com" in text
    assert not leaked_rows
    stored_chars = len(text)
    assert stored_chars <= 4000
    assert stored.get("sql") == "SELECT id, email, amount FROM customers"


async def test_bug_534_oversized_non_row_output_is_capped(monkeypatch, db_session):
    from sqlalchemy import select

    from evolution.engine import EvolutionEngine

    async def _fake_get_session():
        yield db_session

    monkeypatch.setattr("evolution.engine.get_session", _fake_get_session)

    await EvolutionEngine().record_feedback(
        session_id="plan-2", agent_name="InsightAgent", task_type="insight",
        user_prompt="p", agent_output={"narrative": "x" * 500_000}, success=False,
        duration_ms=1.0,
    )
    stored = (await db_session.execute(select(AgentFeedback))).scalars().one().agent_output
    assert len(json.dumps(stored)) <= 4000


# ── BUG-535: a duplicate pattern row made record_success raise forever ──

async def test_bug_535_record_success_tolerates_duplicate_pattern_rows(db_session):
    from sqlalchemy import select

    intent = "show total revenue by month"
    for _ in range(2):
        db_session.add(ExecutionPattern(
            workspace_id="ws-1", pattern_type=PatternType.AGENT_PLAN.value,
            intent_hash=_intent_hash(intent), intent_summary=intent,
            pattern_data={}, success_count=1, avg_duration_ms=10.0,
        ))
    await db_session.commit()

    lib = PatternLibrary(db_session)
    updated = await lib.record_success(
        PatternType.AGENT_PLAN, intent, {"v": 2}, duration_ms=20.0, workspace_id="ws-1",
    )

    assert updated.success_count == 2
    assert updated.avg_duration_ms == pytest.approx(15.0)
    rows = (await db_session.execute(select(ExecutionPattern))).scalars().all()
    assert len(rows) == 2
    assert sorted(r.success_count for r in rows) == [1, 2]
