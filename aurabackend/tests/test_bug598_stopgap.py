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


def _run_child() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AURA_UPLOADS_ROOT"] = tempfile.mkdtemp(prefix="bug598s_")
    env.pop("AURA_STORAGE_BACKEND", None)
    return subprocess.run([sys.executable, "-I", os.path.abspath(__file__)], capture_output=True,
                          text=True, env=env, timeout=180)


def test_a_pipeline_step_cannot_abort_the_process():
    proc = _run_child()
    code = proc.returncode & 0xFFFFFFFF
    assert code != _ABORT, f"the process aborted: {proc.stderr[-300:]!r}"
    assert proc.returncode == 0 and "SURVIVED" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr[-300:])


if __name__ == "__main__":
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
