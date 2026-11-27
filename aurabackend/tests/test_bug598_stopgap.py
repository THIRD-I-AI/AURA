"""BUG-598 stopgap: ``CALL enable_logging(storage='file', ...)`` succeeds on a locked DuckDB
connection (enable_external_access=false does not cover logging) and the next log flush --
at close() -- makes DuckDB abort the whole process. One authenticated pipeline request took the
single gateway worker down. The shared SQL guard now refuses the logging functions on the RAW
text, so quoting or dollar-quoting cannot hide them. Each child runs in a SUBPROCESS: on
unfixed code it aborts, which would take pytest down."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.sql_expression_guard import validate_sql_expression  # noqa: E402

_ABORT = 0xC0000409


@pytest.mark.parametrize("expr", [
    "SELECT * FROM t WHERE (SELECT count(*) FROM enable_logging(storage='file', storage_path='/tmp/x')) = 0",
    "SELECT * FROM t WHERE (SELECT count(*) FROM \"enable_logging\"(storage='file')) = 0",
    "SELECT * FROM t WHERE (SELECT count(*) FROM ENABLE_LOGGING(storage='file')) = 0",
    "SELECT $$x$$ FROM t WHERE (SELECT count(*) FROM enable_logging(storage='file')) = 0",
    "SELECT * FROM duckdb_logs",
    "SELECT * FROM t WHERE (SELECT count(*) FROM truncate_duckdb_logs()) = 0",
    "SELECT * FROM t WHERE (SELECT count(*) FROM disable_logging()) = 0",
    "SELECT current_setting('logging_storage')",
])
def test_the_guard_refuses_every_logging_form(expr):
    with pytest.raises(ValueError):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", [
    "SELECT * FROM t WHERE x > 1",
    "SELECT a, sum(b) OVER (PARTITION BY a) FROM t",
    "WITH c AS (SELECT * FROM t) SELECT * FROM c JOIN t USING (id)",
    "SELECT log_level, logger_name FROM t",
])
def test_ordinary_sql_still_passes(expr):
    validate_sql_expression(expr)


POISON = "SELECT * FROM enable_logging(storage='file', storage_path='/tmp/x')"


def test_the_central_validator_refuses_logging_sql():
    """/execute, the chat and the commander's run_sql use SQLSafetyValidator."""
    from safety import SQLSafetyValidator

    result = SQLSafetyValidator().validate(POISON)
    assert not result.is_valid and any("logging" in e.lower() for e in result.errors)
    assert SQLSafetyValidator().validate("SELECT 1 AS x LIMIT 1").is_valid


def test_creating_a_saved_query_with_logging_sql_is_refused():
    from fastapi import HTTPException

    from api_gateway.routers import queries

    class _Req:  # create_saved_query refuses before it reads the request
        pass

    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(queries.create_saved_query(queries.SavedQueryCreate(name="p", sql=POISON), _Req()))
    assert exc.value.status_code == 400


def test_a_stored_logging_query_never_reaches_the_connection_when_a_tile_runs():
    """Stored SQL that predates the check: the tile reports an error and nothing executes."""
    import asyncio

    from api_gateway.routers.dashboards import _run_tile

    class _Con:
        def __init__(self):
            self.ran = []

        def execute(self, sql, *a, **k):
            self.ran.append(sql)
            raise AssertionError("the logging SQL reached the connection")

    con = _Con()
    out = asyncio.run(_run_tile({"id": "t1", "saved_query_id": "sq1"}, [{"id": "sq1", "sql": POISON, "name": "p"}], con))
    assert out["status"] == "error" and con.ran == []


def _run_child(variant: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AURA_UPLOADS_ROOT"] = tempfile.mkdtemp(prefix="bug598s_")
    env.pop("AURA_STORAGE_BACKEND", None)
    return subprocess.run([sys.executable, "-I", os.path.abspath(__file__), variant], capture_output=True,
                          text=True, env=env, timeout=180)


@pytest.mark.parametrize("variant", ["pipeline", "dashboard_tiles"])
def test_a_poisoned_request_cannot_abort_the_process(variant):
    proc = _run_child(variant)
    code = proc.returncode & 0xFFFFFFFF
    assert code != _ABORT, f"the process aborted: {proc.stderr[-300:]!r}"
    assert proc.returncode == 0 and "SURVIVED" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr[-300:])


def _dashboard_child() -> None:
    """The chain a tenant can run with only a JWT: a stored poison saved query, a dashboard
    with the poison tile and one benign tile, then render -- every tile on ONE locked
    connection, closed at the end (an odd count of failed statements aborts the process)."""
    import asyncio

    from api_gateway.routers.dashboards import _run_tile
    from shared.duckdb_factory import lock_down_connection, new_connection

    queries = [{"id": "p", "sql": POISON, "name": "poison"}, {"id": "b", "sql": "SELECT 1 AS x", "name": "benign"}]
    con = new_connection()
    lock_down_connection(con)
    for i, q in enumerate(queries):
        asyncio.run(_run_tile({"id": f"t{i}", "saved_query_id": q["id"]}, queries, con))
    con.close()
    print("SURVIVED", flush=True)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "dashboard_tiles":
    _dashboard_child()
elif __name__ == "__main__":
    import asyncio

    from pipeline.engine import PipelineEngine
    from pipeline.models import Pipeline, PipelineSink, PipelineSource, ProcessingStep, SinkType, SourceType, StepType

    d = tempfile.mkdtemp(prefix="bug598s_w_").replace("\\", "/")
    expr = ("SELECT * FROM {{prev}} WHERE (SELECT count(*) FROM "
            f"enable_logging(storage='file', storage_path='{d}/logs')) = 0")
    run = asyncio.run(PipelineEngine().execute(Pipeline(
        name="bug598s", source=PipelineSource(type=SourceType.DUCKDB, query="SELECT 1 AS k"),
        steps=[ProcessingStep(type=StepType.CUSTOM_SQL, config={"expression": expr})],
        sink=PipelineSink(type=SinkType.PREVIEW)), preview_only=True, tenant="bug598s"))
    assert run.status.value == "failed", run.status
    print("SURVIVED", flush=True)
