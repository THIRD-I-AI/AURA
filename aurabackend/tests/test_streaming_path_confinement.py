"""BUG-277 / BUG-278 / BUG-283: a streaming pipeline's file watcher, file sink and DuckDB
sink used the paths in the request body as given, so a tenant could watch the parent of
every tenant's uploads (or any absolute path) and write output anywhere on the server."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.streaming import path_confinement
from pipeline.streaming import streaming_engine as engine
from pipeline.streaming.models import StreamPipeline
from shared.storage.base import tenant_slug


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    # keep the sinks' output directories out of the repo's data/ folder
    monkeypatch.setattr(path_confinement, "STREAMING_OUTPUT_ROOT", str(tmp_path / "streaming_output"))
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    yield
    reset_storage_backend()


def _pipeline(source: dict, sinks: list | None = None, tenant: str = "acme") -> StreamPipeline:
    return StreamPipeline(
        id="spipe_test01", name="p", tenant_id=tenant,
        source=source, sinks=sinks or [{"type": "console", "config": {}}],
    )


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


@pytest.mark.parametrize("watch_dir", ["data/uploads", "/", "C:/", "../../etc"])
def test_file_watcher_only_ever_watches_the_tenants_own_uploads(tmp_path, watch_dir):
    source = engine._create_source(_pipeline(
        {"type": "file_watcher", "config": {"watch_dir": watch_dir, "pattern": "*.csv"}}))

    tenant_root = os.path.join(str(tmp_path / "uploads"), tenant_slug("acme"))
    assert _inside(source.watch_dir, tenant_root)
    assert os.path.realpath(source.watch_dir) != os.path.realpath(str(tmp_path / "uploads"))


@pytest.mark.parametrize("pattern", ["**/*.csv", "../*.csv", "sub/*.csv", "..\\*.csv"])
def test_file_watcher_rejects_a_pattern_that_leaves_the_directory(pattern):
    with pytest.raises(ValueError, match="file names only"):
        engine._create_source(_pipeline({"type": "file_watcher", "config": {"pattern": pattern}}))


def test_two_tenants_watch_different_directories():
    a = engine._create_source(_pipeline({"type": "file_watcher", "config": {}}, tenant="acme"))
    b = engine._create_source(_pipeline({"type": "file_watcher", "config": {}}, tenant="evil"))
    assert os.path.realpath(a.watch_dir) != os.path.realpath(b.watch_dir)


@pytest.mark.parametrize("output_dir", ["/etc", "C:/Windows", "../../somewhere", "data/uploads/acme"])
def test_file_sink_writes_only_under_the_tenants_streaming_output(output_dir):
    pipeline = _pipeline({"type": "simulated", "config": {}},
                         sinks=[{"type": "file", "config": {"output_dir": output_dir}}])

    sink = engine._create_sink(pipeline.sinks[0], pipeline)

    expected = os.path.join(path_confinement.STREAMING_OUTPUT_ROOT, tenant_slug("acme"), "spipe_test01")
    assert os.path.realpath(sink._output_dir) == os.path.realpath(expected)


@pytest.mark.parametrize("path", ["/tmp/evil.duckdb", "../../uploads/acme2/data.duckdb", "C:/x/y.duckdb"])
def test_duckdb_sink_file_is_placed_under_the_tenants_streaming_output(path):
    pipeline = _pipeline({"type": "simulated", "config": {}},
                         sinks=[{"type": "database", "config": {"path": path}}])

    sink = engine._create_sink(pipeline.sinks[0], pipeline)

    root = os.path.join(path_confinement.STREAMING_OUTPUT_ROOT, tenant_slug("acme"), "spipe_test01")
    assert _inside(sink.config["path"], root)
    assert os.path.basename(sink.config["path"]) == os.path.basename(path)


def test_in_memory_duckdb_sink_is_left_alone():
    pipeline = _pipeline({"type": "simulated", "config": {}}, sinks=[{"type": "database", "config": {}}])
    sink = engine._create_sink(pipeline.sinks[0], pipeline)
    assert sink.config.get("path", ":memory:") == ":memory:"
