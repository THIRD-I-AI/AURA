import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.storage import get_storage_backend, reset_storage_backend
from shared.storage.base import ObjectInfo
from shared.storage.local import LocalBackend


@pytest.fixture(autouse=True)
def _reset():
    reset_storage_backend()
    yield
    reset_storage_backend()


def _local(tmp_path):
    return LocalBackend(root=str(tmp_path))


def test_local_write_then_read(tmp_path):
    b = _local(tmp_path)
    info = b.write("acme", "sales.csv", b"region,revenue\nN,1\n")
    assert isinstance(info, ObjectInfo)
    assert info.name == "sales.csv"
    assert b.read("acme", "sales.csv") == b"region,revenue\nN,1\n"


def test_local_list_is_tenant_scoped(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    b.write("globex", "b.csv", b"y\n2\n")
    acme = {o.name for o in b.list("acme")}
    assert acme == {"a.csv"}
    assert b.exists("acme", "a.csv") is True
    assert b.exists("acme", "b.csv") is False


def test_local_delete(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    assert b.delete("acme", "a.csv") is True
    assert b.list("acme") == []


def test_local_duckdb_uri_is_local_path(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    uri = b.duckdb_uri("acme", "a.csv")
    assert uri.endswith("a.csv")
    assert "s3://" not in uri


def test_local_configure_duckdb_is_noop(tmp_path):
    import duckdb
    con = duckdb.connect(":memory:")
    _local(tmp_path).configure_duckdb(con)  # must not raise
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_get_storage_backend_default_local(monkeypatch):
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    assert isinstance(get_storage_backend(), LocalBackend)


def test_get_storage_backend_unknown_raises(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "nope")
    with pytest.raises(ValueError, match="Unknown storage backend"):
        get_storage_backend()


def test_local_rejects_traversal_filename(tmp_path):
    b = LocalBackend(root=str(tmp_path))
    for bad in ("../evil.csv", "a/b.csv", "a\\b.csv", "", ".", ".."):
        with pytest.raises(ValueError):
            b.write("acme", bad, b"x\n1\n")
