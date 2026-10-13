"""BUG-434: repeated result column names must not overwrite each other in records."""
from __future__ import annotations

import asyncio

import duckdb

from agents.base import AgentContext, AgentStatus
from agents.specialists.execution_agent import ExecutionAgent


def _run(sql: str):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE customers AS SELECT 1 AS id, 'alice' AS name")
    con.execute("CREATE TABLE products AS SELECT 1 AS id, 'widget' AS name, 'spare' AS name_2")
    ctx = AgentContext(user_prompt="q", task_description="exec",
                       upstream_results={"sql": sql}, metadata={"duckdb_con": con})
    return asyncio.run(ExecutionAgent().execute(ctx))


def test_a_join_with_two_name_columns_keeps_both_values():
    res = _run('SELECT c."name", p."name", 3 AS qty FROM customers c JOIN products p ON c.id = p.id')

    assert res.status == AgentStatus.SUCCESS
    assert res.output["columns"] == ["name", "name_2", "qty"]
    assert res.output["records"] == [{"name": "alice", "name_2": "widget", "qty": 3}]
    assert res.output["rows"] == [["alice", "widget", 3]]


def test_a_suffix_never_collides_with_a_real_column():
    res = _run('SELECT c."name", p."name", p."name_2" FROM customers c JOIN products p ON c.id = p.id')

    columns = res.output["columns"]
    assert len(set(columns)) == 3 and columns[0] == "name" and columns[2] == "name_2"
    assert sorted(res.output["records"][0].values()) == ["alice", "spare", "widget"]
