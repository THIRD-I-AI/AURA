"""BUG-598: ``enable_logging(storage='file', ...)`` succeeds on a connection locked by
``lock_down_connection`` -- ``enable_external_access=false`` and ``lock_configuration`` do not
govern DuckDB's logging functions -- and bricks it: every later statement on that connection
fails ("Cannot access directory ... file system operations are disabled by configuration"),
including ``SET logging_storage='memory'``, so nothing can undo it. Releasing the connection
(close, garbage collection or process exit) then ABORTS THE WHOLE PROCESS when the number of
failed statements was odd (exit 0xC0000409 on Windows; the gateway runs one uvicorn worker, so
that is every tenant). Measured on duckdb 1.5.2 and 1.5.5: poison + 0 failed statements
survives, + 1 aborts, + 2 survives, + 3 aborts, whatever the release method.

There is no DuckDB setting that prevents it and no SQL-level recovery, so the only control is
to refuse the call before the SQL reaches a locked connection (``reject_logging_sql``: the raw
text of #821, then DuckDB's own parser and lexer, which also refuse the functions that run a
string as SQL and so could build the name at run time), and to refuse a view or macro that
calls it inside a tenant's own database file (``reject_stored_logging_calls``). A ``close()``
wrapper that resets the log storage was tried and does not work: its statements fail and only
change the parity.

Every scenario that can abort runs in a SUBPROCESS, so the test runner survives; each aborts
on the code before this fix and survives here.
"""
from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import tempfile

import duckdb
import pytest

# The children run in isolated mode (-I): they get the parent's import path (so the same
# duckdb) and the backend directory explicitly.
for _p in reversed([p for p in os.environ.get("BUG598_SYS_PATH", "").split(os.pathsep) if p]):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.sql_expression_guard import (  # noqa: E402
    _LOGGING_ABUSE_PATTERN,
    reject_logging_calls,
    reject_logging_sql,
)

PLAIN = "SELECT * FROM enable_logging(storage='file', storage_path='/tmp/x')"
# The name is never written out, so no raw-text check can see it.
SPLIT = "SELECT * FROM query('SELECT * FROM enable_' || 'logging(storage=''file'', storage_path=''/tmp/x'')')"
CHR_BUILT = ("SELECT * FROM query('SELECT * FROM enable' || chr(95) || "
             "'logging(storage=''file'', storage_path=''/tmp/x'')')")

# ── the check itself ────────────────────────────────────────────────────────────────────

CALLS_REFUSED = {
    "plain": PLAIN,
    "quoted and upper case": 'SELECT * FROM "ENABLE_LOGGING"(storage=\'file\')',
    "schema qualified": "SELECT * FROM system.main.enable_logging(storage='file')",
    "CALL": "CALL enable_logging(storage='file', storage_path='/tmp/x')",
    "PREPARE and EXECUTE": "PREPARE p AS SELECT * FROM enable_logging(storage='file'); EXECUTE p",
    "stacked statement": "SELECT 1; SELECT * FROM enable_logging(storage='file')",
    "dollar-quoted decoy": "SELECT $$x$$, 1 FROM enable_logging(storage='file')",
    "comment before the parenthesis": "SELECT * FROM enable_logging /* c */ (storage='file')",
    "disable_logging": "SELECT * FROM disable_logging()",
    "truncate_duckdb_logs": "SELECT * FROM truncate_duckdb_logs()",
    "name split by a constant": SPLIT,
    "name built with chr()": CHR_BUILT,
    "query_table": "SELECT * FROM query_table('t')",
    "json_execute_serialized_sql": "SELECT * FROM json_execute_serialized_sql('{}')",
    "dynamic SQL after a stacked statement": "SELECT 1; SELECT * FROM query('SELECT 1')",
}


@pytest.mark.parametrize("sql", list(CALLS_REFUSED.values()), ids=list(CALLS_REFUSED))
def test_a_call_of_a_logging_function_is_refused_however_it_is_written(sql):
    with pytest.raises(ValueError):
        reject_logging_sql(sql)
    with pytest.raises(ValueError):
        reject_logging_calls(sql)


def test_the_raw_text_layer_alone_misses_a_name_built_at_run_time():
    """Why the parser layer exists: the regex of #821 cannot see a name that is never
    written out, and such a call bricks a locked connection all the same."""
    for sql in (SPLIT, CHR_BUILT):
        assert _LOGGING_ABUSE_PATTERN.search(sql) is None
        with pytest.raises(ValueError):
            reject_logging_sql(sql)


ORDINARY = {
    "aggregate": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY 1",
    "window and cte": "WITH c AS (SELECT * FROM t) SELECT a, sum(b) OVER (PARTITION BY a) FROM c",
    "a CTE called query, with a column list": "WITH query(a, b) AS (SELECT 1, 2) SELECT a, b FROM query",
    "join and union": "SELECT * FROM a JOIN b USING (k) UNION ALL SELECT * FROM c",
    "template placeholder": "SELECT * FROM {{prev}} WHERE x > 1",
}


@pytest.mark.parametrize("sql", list(ORDINARY.values()), ids=list(ORDINARY))
def test_ordinary_analytic_sql_passes_both_layers(sql):
    reject_logging_sql(sql)
    reject_logging_calls(sql)


@pytest.mark.parametrize("sql", [
    "SELECT enable_logging FROM cfg",
    "SELECT logging_level, count(*) FROM app_logs GROUP BY 1",
    "SELECT 'enable_logging' AS s",
], ids=["column", "column logging_level", "string"])
def test_the_precise_layer_leaves_a_column_or_string_that_only_shares_a_name_alone(sql):
    """Only a CALL is refused by ``reject_logging_calls``. The raw-text layer (#821) refuses any
    mention, an accepted price for the SQL it fronts; the stored-object scan below must not
    refuse a tenant's log-style table, so it uses the precise layer alone."""
    reject_logging_calls(sql)
    with pytest.raises(ValueError):
        reject_logging_sql(sql)


# ── a view or macro stored in the tenant's own file ─────────────────────────────────────

def _scan(*statements: str):
    from shared.duckdb_factory import reject_stored_logging_calls

    con = duckdb.connect()
    try:
        for s in statements:
            con.execute(s)
        reject_stored_logging_calls(con)
    finally:
        con.close()  # the creating connection is unlocked, so its log file is writable


def _stored_poison(log: str) -> dict[str, list[str]]:
    """Statements a tenant could store in its own file. CREATE VIEW binds, which runs the call
    once on the (unlocked) connection that builds the file, so the log path must be writable."""
    call = f"enable_logging(storage='file', storage_path='{log}')"
    return {
        "view": [f"CREATE VIEW boom AS SELECT * FROM {call}"],
        "table macro": [f"CREATE MACRO boom() AS TABLE SELECT * FROM {call}"],
        "view with a run-time name": [
            "CREATE VIEW boom AS SELECT * FROM query('SELECT * FROM enable_' || "
            f"'logging(storage=''file'', storage_path=''{log}'')')"],
    }


@pytest.mark.parametrize("kind", ["view", "table macro", "view with a run-time name"])
def test_a_stored_view_or_macro_that_calls_a_logging_function_is_refused(kind, tmp_path):
    with pytest.raises(ValueError):
        _scan(*_stored_poison(tmp_path.as_posix() + "/logs")[kind])


def test_a_stored_view_or_macro_that_only_shares_a_name_is_left_alone():
    _scan("CREATE TABLE app_logs AS SELECT 1 AS enable_logging, 2 AS logging_level",
          "CREATE VIEW v AS SELECT enable_logging, logging_level FROM app_logs",
          "CREATE MACRO m(a) AS a + 1")
    _scan()  # an empty database


# ── every entry point, in-process, with a connection that fails the test if SQL reaches it ──

class _GuardedCon:
    """A connection standing in for the locked one: SQL reaching it is the bug."""

    def __init__(self):
        self.ran: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.ran.append(sql)
        raise AssertionError("logging SQL reached the connection")

    def interrupt(self):
        pass


@pytest.mark.parametrize("sql", [PLAIN, SPLIT], ids=["plain", "split"])
def test_the_chat_execution_agent_refuses_model_written_logging_sql(sql):
    from agents.base import AgentContext, AgentStatus
    from agents.specialists.execution_agent import ExecutionAgent

    con = _GuardedCon()
    ctx = AgentContext(user_prompt="q", task_description="exec", upstream_results={"sql": sql},
                       metadata={"duckdb_con": con})
    res = asyncio.run(ExecutionAgent().execute(ctx))

    assert res.status == AgentStatus.FAILED and con.ran == []


def test_the_chat_execution_agent_still_runs_ordinary_sql():
    from agents.base import AgentContext, AgentStatus
    from agents.specialists.execution_agent import ExecutionAgent

    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT * FROM range(5) r(i)")
    ctx = AgentContext(user_prompt="q", task_description="exec",
                       upstream_results={"sql": "SELECT sum(i) AS s FROM t"}, metadata={"duckdb_con": con})
    res = asyncio.run(ExecutionAgent().execute(ctx))

    assert res.status == AgentStatus.SUCCESS and res.output["records"] == [{"s": 10}]


@pytest.mark.parametrize("sql", [PLAIN, SPLIT], ids=["plain", "split"])
def test_the_dpc_verifier_refuses_model_written_logging_sql(sql):
    from agents.langgraph_orchestrator import _dpc_registry

    con = _GuardedCon()
    with pytest.raises(ValueError):
        asyncio.run(_dpc_registry(con).call("execute_sql", query=sql))
    assert con.ran == []


def test_the_dpc_verifier_still_runs_ordinary_sql():
    from agents.langgraph_orchestrator import _dpc_registry

    con = duckdb.connect()
    out = asyncio.run(_dpc_registry(con).call("execute_sql", query="SELECT 41 + 1 AS answer"))
    assert out["rows"] == [[42]]


@pytest.mark.parametrize("sql", [PLAIN, SPLIT], ids=["plain", "split"])
def test_the_validator_behind_execute_chat_and_the_commander_refuses_both_forms(sql):
    from safety import SQLSafetyValidator

    result = SQLSafetyValidator().validate(sql)
    assert not result.is_valid and any("logging" in e.lower() for e in result.errors)


def test_the_scheduler_refuses_a_stored_logging_query_before_it_runs(tmp_path, monkeypatch):
    from api_gateway.routers import queries

    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    for sql in (PLAIN, SPLIT):
        with pytest.raises(ValueError):
            asyncio.run(queries._execute_saved_query_sql(sql, "ws1"))


def test_a_saved_query_that_builds_the_name_at_run_time_is_refused_at_creation():
    from fastapi import HTTPException

    from api_gateway.routers import queries

    class _Req:  # create_saved_query refuses before it reads the request
        pass

    with pytest.raises(HTTPException) as exc:
        asyncio.run(queries.create_saved_query(queries.SavedQueryCreate(name="p", sql=SPLIT), _Req()))
    assert exc.value.status_code == 400


def test_a_stored_run_time_named_query_never_reaches_the_connection_when_a_tile_runs():
    from api_gateway.routers.dashboards import _run_tile

    con = _GuardedCon()
    out = asyncio.run(_run_tile({"id": "t1", "saved_query_id": "sq1"},
                                [{"id": "sq1", "sql": SPLIT, "name": "p"}], con))
    assert out["status"] == "error" and con.ran == []


def _connector(path):
    from connectors.base import ConnectorConfig
    from connectors.base import SourceType as CSourceType
    from connectors.duckdb_connector import DuckDBConnector

    return DuckDBConnector(ConnectorConfig(source_type=CSourceType.DUCKDB, name="x", connection_string=str(path)))


def _tenant_file(path, *statements):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sales AS SELECT * FROM range(5) t(i)")
    for s in statements:
        con.execute(s)
    con.close()
    return path


def test_the_connector_refuses_a_logging_query_and_keeps_serving_ordinary_ones(tmp_path):
    async def go():
        conn = _connector(_tenant_file(tmp_path / "t.duckdb"))
        assert await conn.connect() is True
        for sql in (PLAIN, SPLIT):
            with pytest.raises(ValueError):
                await conn.execute_query(sql, raise_errors=True)
            assert await conn.execute_query(sql) == []
        assert await conn.execute_query("SELECT count(*) AS n FROM sales") == [{"n": 5}]
        await conn.disconnect()

    asyncio.run(go())


def test_the_connector_refuses_a_file_whose_stored_view_calls_a_logging_function(tmp_path):
    poisoned = _tenant_file(
        tmp_path / "p.duckdb",
        f"CREATE VIEW boom AS SELECT * FROM enable_logging(storage='file', storage_path='{tmp_path.as_posix()}/l')")

    async def go():
        conn = _connector(poisoned)
        assert await conn.connect() is False
        assert conn._conn is None and not conn._is_connected
        return conn

    conn = asyncio.run(go())
    assert asyncio.run(conn.execute_query("SELECT 1")) == []  # nothing is connected


def test_the_connector_still_opens_a_file_with_a_log_style_column_and_view(tmp_path):
    """The stored-object scan reads calls only, so a tenant's log table -- with columns that
    happen to be called enable_logging and logging_level -- connects and is queried as usual."""
    ok = _tenant_file(tmp_path / "ok.duckdb",
                      "CREATE TABLE app_logs AS SELECT 1 AS enable_logging, 'INFO' AS logging_level",
                      "CREATE VIEW levels AS SELECT logging_level, enable_logging FROM app_logs")

    async def go():
        conn = _connector(ok)
        assert await conn.connect() is True
        assert await conn.execute_query("SELECT count(*) AS n FROM levels") == [{"n": 1}]
        await conn.disconnect()

    asyncio.run(go())


# ── the connection factory hands out a bare DuckDB connection ───────────────────────────
#
# An earlier design wrapped it so close() could reset the log storage; the wrapper closed the
# real connection when it was collected, which broke any result or cursor that outlived the
# function that made it (a streaming Arrow reader even returned 0 rows without an error).

def _lazy_result():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    con.execute("CREATE TABLE t AS SELECT * FROM range(5) r(i)")
    return con.execute("SELECT * FROM t ORDER BY i")


def _arrow_reader():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    con.execute("CREATE TABLE t AS SELECT * FROM range(200000) r(i)")
    return con.execute("SELECT * FROM t").to_arrow_reader(1000)


def _cursor():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    con.execute("CREATE TABLE t AS SELECT 7 AS a")
    return con.cursor()


def _relation():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    con.execute("CREATE TABLE t AS SELECT 7 AS a")
    return con.sql("SELECT a FROM t")


def test_a_lazy_result_returned_from_the_function_that_made_the_connection_stays_usable():
    result = _lazy_result()
    gc.collect()
    assert result.fetchall() == [(0,), (1,), (2,), (3,), (4,)]


def test_a_streaming_arrow_reader_outlives_its_connection_and_returns_every_row():
    reader = _arrow_reader()
    gc.collect()
    assert reader.read_all().num_rows == 200000


def test_a_cursor_outlives_the_connection_it_came_from():
    cursor = _cursor()
    gc.collect()
    assert cursor.execute("SELECT a FROM t").fetchall() == [(7,)]


def test_a_relation_outlives_the_connection_it_came_from():
    relation = _relation()
    gc.collect()
    assert relation.fetchall() == [(7,)]


def test_new_connection_returns_a_real_duckdb_connection():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    try:
        assert type(con) is duckdb.DuckDBPyConnection
        assert isinstance(con, duckdb.DuckDBPyConnection)
    finally:
        con.close()


# ── the abort itself: each scenario below aborts on the code before this fix ────────────

_ABORT = 0xC0000409


def _run_child(variant: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AURA_UPLOADS_ROOT"] = tempfile.mkdtemp(prefix="bug598_up_")
    env["BUG598_SYS_PATH"] = os.pathsep.join(p for p in sys.path if p)
    env.pop("AURA_STORAGE_BACKEND", None)
    return subprocess.run([sys.executable, "-I", os.path.abspath(__file__), variant],
                          capture_output=True, text=True, env=env, timeout=240)


@pytest.mark.parametrize("variant", [
    "dashboard_tiles_run_time_name",
    "chat_agent",
    "chat_verifier",
    "connector_stored_view",
])
def test_a_request_that_builds_a_logging_call_cannot_abort_the_process(variant):
    proc = _run_child(variant)
    code = proc.returncode & 0xFFFFFFFF
    assert code != _ABORT, f"[{variant}] the process aborted: {proc.stderr[-300:]!r}"
    assert proc.returncode == 0 and "SURVIVED" in proc.stdout, (
        f"[{variant}]", proc.returncode, proc.stdout[-300:], proc.stderr[-300:])


def _locked_connection():
    from shared.duckdb_factory import lock_down_connection, new_connection

    con = new_connection()
    lock_down_connection(con)
    return con


def _release(con) -> None:
    """What every route does at the end: one more statement, then close. On a poisoned
    connection that is the odd failed statement that makes the close abort."""
    try:
        con.execute("SELECT 1").fetchall()
    except duckdb.Error:
        pass
    con.close()


def _child_dashboard_tiles_run_time_name() -> None:
    """The dashboard render chain with a stored query that builds the name at run time (the
    raw-text check of #821 passed it, and so did the saved-query create): every tile runs on
    ONE locked connection, then it is closed."""
    from api_gateway.routers.dashboards import _run_tile

    queries = [{"id": "p", "sql": SPLIT, "name": "poison"}, {"id": "b", "sql": "SELECT 1 AS x", "name": "benign"}]
    con = _locked_connection()
    out = [asyncio.run(_run_tile({"id": f"t{i}", "saved_query_id": q["id"]}, queries, con))
           for i, q in enumerate(queries)]
    assert out[0]["status"] == "error" and out[1]["status"] == "success", out
    con.close()


def _child_chat_agent() -> None:
    """The chat flow's model-written SQL runs on a locked connection that is then released.
    Before the fix the run-time-built call executes, the release's one more statement fails and
    the close aborts the process; the assertion comes after the release for that reason."""
    from agents.base import AgentContext, AgentStatus
    from agents.specialists.execution_agent import ExecutionAgent

    con = _locked_connection()
    ctx = AgentContext(user_prompt="q", task_description="exec", upstream_results={"sql": SPLIT},
                       metadata={"duckdb_con": con})
    res = asyncio.run(ExecutionAgent().execute(ctx))
    _release(con)
    assert res.status == AgentStatus.FAILED


def _child_chat_verifier() -> None:
    """The same for the DPC verifier's SQL, which runs on the connection the answer came from."""
    from agents.langgraph_orchestrator import _dpc_registry

    con = _locked_connection()
    try:
        asyncio.run(_dpc_registry(con).call("execute_sql", query=SPLIT))
        refused = False
    except ValueError:
        refused = True
    _release(con)
    assert refused


def _child_connector_stored_view() -> None:
    """A view in the tenant's own .duckdb calls enable_logging(file); the caller's SQL is
    innocent. Before the fix: query the view, list tables, disconnect -> abort."""
    d = tempfile.mkdtemp(prefix="bug598_c_").replace("\\", "/")
    path = f"{d}/tenant.duckdb"
    con = duckdb.connect(path)
    con.execute("CREATE TABLE sales AS SELECT * FROM range(5) t(i)")
    con.execute(f"CREATE VIEW boom AS SELECT * FROM enable_logging(storage='file', storage_path='{d}/logs')")
    con.close()

    async def go():
        conn = _connector(path)
        connected = await conn.connect()
        await conn.execute_query("SELECT * FROM boom")
        await conn.list_tables()
        await conn.disconnect()
        return connected

    assert asyncio.run(go()) is False


# ── every module that locks a connection has been audited ───────────────────────────────

# lock_down_connection marks the point after which tenant, stored or LLM SQL runs, and a
# logging call on that connection aborts the process. This lists every module that calls it and
# how its SQL is checked. A new entry here is the moment to make sure the new module's SQL
# goes through reject_logging_sql (or validate_sql_expression) before it reaches the connection.
_LOCKED_CONNECTION_USERS = {
    "api_gateway/routers/chat.py":
        "the chat agents (ExecutionAgent, DPC verifier: reject_logging_sql) and the commander "
        "(run_sql: SQLSafetyValidator)",
    "api_gateway/routers/dashboards.py": "the tile runner: reject_logging_sql",
    "api_gateway/routers/etl.py": "transform steps: validate_sql_expression",
    "api_gateway/routers/queries.py":
        "/execute: SQLSafetyValidator; the scheduler: reject_logging_sql",
    "connectors/duckdb_connector.py":
        "execute_query: reject_logging_sql; connect: reject_stored_logging_calls",
    "pipeline/engine.py":
        "steps and DuckDB source: validate_sql_expression; the source connection runs one statement",
}


def test_every_module_that_locks_a_connection_has_been_audited_for_logging_sql():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    imports = re.compile(
        r"^\s*from\s+\S+\s+import\s+.*\block_down_connection\b|\bduckdb_factory\.lock_down_connection\b",
        re.MULTILINE)
    users = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"tests", ".venv", "node_modules", "__pycache__", "data"}]
        for name in filenames:
            path = Path(dirpath) / name
            if name.endswith(".py") and imports.search(path.read_text(encoding="utf-8", errors="ignore")):
                users.add(path.relative_to(root).as_posix())
    assert users == set(_LOCKED_CONNECTION_USERS), (
        "the modules that lock a DuckDB connection changed: make sure the SQL each one runs is "
        f"checked by reject_logging_sql, then list it in _LOCKED_CONNECTION_USERS. now: {sorted(users)}")


def _child_pipeline_stored_view_source() -> None:
    """The pipeline's DuckDB source opens the tenant's file on a connection that runs exactly ONE
    statement, so a poisoned view in it is never followed by a failed statement and the release
    survives. This pins that property (it holds on main too); a second statement on that
    connection would turn it into an abort."""
    from pipeline.engine import PipelineEngine
    from pipeline.models import Pipeline, PipelineSink, PipelineSource, SinkType, SourceType
    from shared.storage import get_storage_backend

    d = tempfile.mkdtemp(prefix="bug598_s_").replace("\\", "/")
    path = f"{d}/w.duckdb"
    con = duckdb.connect(path)
    con.execute("CREATE TABLE sales AS SELECT * FROM range(5) t(i)")
    con.execute(f"CREATE VIEW boom AS SELECT * FROM enable_logging(storage='file', storage_path='{d}/logs')")
    con.close()
    with open(path, "rb") as fh:
        get_storage_backend().write("bug598", "warehouse.duckdb", fh.read())
    for source in ({"table": "boom"}, {"query": "SELECT * FROM ext_source_data.boom"}):
        run = asyncio.run(PipelineEngine().execute(Pipeline(
            name="bug598",
            source=PipelineSource(type=SourceType.DUCKDB, connection={"database": "warehouse.duckdb"}, **source),
            sink=PipelineSink(type=SinkType.PREVIEW)), preview_only=True, tenant="bug598"))
        assert run.status.value in ("success", "failed"), run.status


def test_the_pipeline_duckdb_source_over_a_poisoned_view_cannot_abort_the_process():
    """A pin, not a regression of this fix: the source connection runs exactly one statement, so
    the poison is never followed by a failed one. It holds on main too; this keeps it true."""
    proc = _run_child("pipeline_stored_view_source")
    assert proc.returncode == 0 and "SURVIVED" in proc.stdout, (
        proc.returncode, proc.stdout[-300:], proc.stderr[-300:])


if __name__ == "__main__" and len(sys.argv) > 1:
    globals()[f"_child_{sys.argv[1]}"]()
    print("SURVIVED", flush=True)
