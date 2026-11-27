"""BUG-598: ``CALL enable_logging(storage='file', ...)`` (or the table-function form)
succeeds on a connection that has been locked (``enable_external_access=false``), and
DuckDB then *aborts the whole process* -- a fail-fast Python cannot catch, Windows exit
0xC0000409 -- at the next log flush, which happens when the connection is closed OR when
a leaked connection is garbage collected. One POST /pipeline/execute took the single
uvicorn worker (the whole gateway) down, repeatably.

The fix is ``shared/duckdb_factory.safe_close`` (a throwaway query that absorbs the
pending broken flush, then a single reset of the log storage, then the close), applied
by wrapping every ``new_connection`` so close -- and garbage collection -- is safe, plus
the same on the two raw ``duckdb.connect`` paths. Verified on duckdb 1.5.2 and 1.5.5.

The crash would take pytest itself down, so every case runs in a SUBPROCESS and the
test asserts the child exited cleanly. Each child reaches ``enable_logging`` on a locked
connection; on origin/main the child aborts, here it survives.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

_ABORT = 0xC0000409  # STATUS_STACK_BUFFER_OVERRUN / DuckDB fail-fast on Windows


def _run_child(variant: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AURA_UPLOADS_ROOT"] = tempfile.mkdtemp(prefix="bug598_up_")
    env.pop("AURA_STORAGE_BACKEND", None)
    return subprocess.run(
        [sys.executable, "-I", os.path.abspath(__file__), variant],
        capture_output=True, text=True, env=env, timeout=180,
    )


@pytest.mark.parametrize("variant", [
    "custom_sql_step",   # the real control: the AST guard (BUG-599) refuses enable_logging up front
    "safe_close_after_poison",     # defence in depth: a locked new_connection, poisoned, closed
    "garbage_collection_after_poison",  # defence in depth: dropped without close -> finalised safely
])
def test_file_logging_on_a_locked_connection_cannot_abort_the_process(variant):
    proc = _run_child(variant)
    assert proc.returncode != _ABORT and proc.returncode & 0xFFFFFFFF != _ABORT, (
        f"[{variant}] the process aborted (exit {proc.returncode & 0xFFFFFFFF:#x}); "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-400:]!r}"
    )
    assert proc.returncode == 0, f"[{variant}] non-zero exit {proc.returncode}: {proc.stderr[-400:]!r}"
    assert "SURVIVED" in proc.stdout, f"[{variant}] no survival sentinel: {proc.stdout!r}"


# ── child process ──────────────────────────────────────────────────────────────

def _child(variant: str) -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import gc

    import duckdb

    def poison_sql(d: str) -> str:
        return f"SELECT (SELECT count(*) FROM enable_logging(storage='file', storage_path='{d}/logs')) AS a"

    if variant in ("safe_close_after_poison", "garbage_collection_after_poison"):
        from shared.duckdb_factory import lock_down_connection, new_connection
        d = tempfile.mkdtemp(prefix="bug598_c_").replace("\\", "/")
        con = new_connection()
        lock_down_connection(con)
        try:
            con.execute(poison_sql(d)).fetchall()
        except duckdb.Error:
            pass
        if variant == "safe_close_after_poison":
            con.close()
        else:
            del con
            gc.collect()
        print("SURVIVED", flush=True)
        return

    # custom_sql_step: a CUSTOM_SQL step runs caller SQL on the run connection after the
    # lockdown -- the vector that aborts on origin/main, where the keyword guard passes
    # enable_logging. Here the AST guard (BUG-599) refuses it before it runs: the run
    # fails and the process lives. (This is prevention; safe_close alone cannot be relied
    # on once the poisoned connection has run several more queries -- hence the guard.)
    import asyncio

    from pipeline.engine import PipelineEngine
    from pipeline.models import (
        Pipeline,
        PipelineSink,
        PipelineSource,
        ProcessingStep,
        SinkType,
        SourceType,
        StepType,
    )

    d = tempfile.mkdtemp(prefix="bug598_w_").replace("\\", "/")
    expr = f"SELECT * FROM {{{{prev}}}} WHERE ({poison_sql(d)}) = 0"
    pipeline = Pipeline(
        name="bug598",
        source=PipelineSource(type=SourceType.DUCKDB, query="SELECT 1 AS k"),
        steps=[ProcessingStep(type=StepType.CUSTOM_SQL, config={"expression": expr})],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = asyncio.run(PipelineEngine().execute(pipeline, preview_only=True, tenant="bug598-attacker"))
    assert run.status.value == "failed", run.status  # refused by the guard, not executed
    print("SURVIVED", flush=True)


if __name__ == "__main__":
    _child(sys.argv[1])
