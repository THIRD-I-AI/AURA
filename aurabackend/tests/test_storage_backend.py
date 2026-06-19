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


boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture
def _s3_env(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_BUCKET", "aura-test")
    monkeypatch.setenv("AURA_S3_REGION", "us-east-1")
    monkeypatch.setenv("AURA_S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AURA_S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AURA_S3_PREFIX", "uploads")
    from shared.config import reload_settings  # see Task 3 Step 3 note
    reload_settings()
    reset_storage_backend()


class TestS3Backend:
    def test_write_list_read_delete(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            b = S3Backend()
            info = b.write("acme", "sales.csv", b"region,revenue\nN,1\n")
            assert info.name == "sales.csv"
            assert info.fingerprint  # etag|size
            assert b.read("acme", "sales.csv") == b"region,revenue\nN,1\n"
            names = {o.name for o in b.list("acme")}
            assert names == {"sales.csv"}
            assert b.exists("acme", "sales.csv") is True
            assert b.delete("acme", "sales.csv") is True
            assert b.list("acme") == []

    def test_list_is_tenant_scoped(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            b = S3Backend()
            b.write("acme", "a.csv", b"x\n1\n")
            b.write("globex", "b.csv", b"y\n2\n")
            assert {o.name for o in b.list("acme")} == {"a.csv"}
            assert {o.name for o in b.list("globex")} == {"b.csv"}

    def test_duckdb_uri_shape(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            assert (
                S3Backend().duckdb_uri("acme", "a.csv")
                == "s3://aura-test/uploads/acme/a.csv"
            )

    def test_configure_duckdb_runs_create_secret(self, _s3_env):
        from unittest.mock import MagicMock

        from shared.storage.s3 import S3Backend
        con = MagicMock()
        S3Backend().configure_duckdb(con)
        sql = " ".join(call.args[0] for call in con.execute.call_args_list)
        assert "httpfs" in sql
        assert "CREATE OR REPLACE SECRET" in sql
        assert "TYPE s3" in sql.lower() or "TYPE S3" in sql
