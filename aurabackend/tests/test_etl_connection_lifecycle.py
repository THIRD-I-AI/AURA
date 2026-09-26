"""BUG-194: ETL preview / suggest leaked their DuckDB connection when the source read failed,
and a COPY that failed part-way left a partial output file behind."""
from __future__ import annotations

import asyncio

import pytest

from api_gateway.routers import etl


class _Con:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture()
def spy(monkeypatch, tmp_path):
    con = _Con()
    # etl imports these inside the handler, so patch them where they are defined.
    monkeypatch.setattr("shared.duckdb_factory.new_connection", lambda: con)

    def boom(*a, **k):
        raise RuntimeError("source read failed")

    monkeypatch.setattr("shared.data_utils.smart_load_file", boom)

    class _Backend:
        def exists(self, tenant, name):
            return True

        def duckdb_uri(self, tenant, name):
            return str(tmp_path / name)

    monkeypatch.setattr(etl, "get_storage_backend", lambda: _Backend())
    return con


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


def test_preview_closes_the_connection_when_the_read_fails(spy):
    out = asyncio.run(etl.etl_preview_source({"source_file": "a.csv"}, _request()))
    assert out["status"] == "error"
    assert spy.closed, "the DuckDB connection leaked on the failure path"


def test_copy_or_cleanup_removes_a_partial_file_and_reraises(tmp_path):
    target = tmp_path / "out.csv"

    class _PartialCon:
        def execute(self, sql):
            target.write_text("half a file")
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        etl._copy_or_cleanup(_PartialCon(), "COPY ...", str(target))
    assert not target.exists(), "the partial output file must be removed"


def test_copy_or_cleanup_leaves_a_successful_file_alone(tmp_path):
    target = tmp_path / "out.csv"

    class _OkCon:
        def execute(self, sql):
            target.write_text("done")

    etl._copy_or_cleanup(_OkCon(), "COPY ...", str(target))
    assert target.read_text() == "done"
