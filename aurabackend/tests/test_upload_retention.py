"""Verification uploads expire, and each tenant's uploads are capped."""
from __future__ import annotations

import os
import time

from shared import upload_retention as ur


def _touch(path, age_hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a,b\n1,2\n")
    old = time.time() - age_hours * 3600
    os.utime(path, (old, old))
    return path


def test_old_verification_uploads_expire_and_nothing_else_does(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_VERIFY_UPLOAD_TTL_HOURS", "24")
    old = _touch(tmp_path / "t1" / "verify_05d29aeb_upload.csv", 30)
    fresh = _touch(tmp_path / "t1" / "verify_0829aaf7_upload.csv", 2)
    customer_old = _touch(tmp_path / "t1" / "sales_2025.csv", 300)
    look_alike = _touch(tmp_path / "t2" / "verify_report.csv", 300)
    nested = _touch(tmp_path / "t2" / "sub" / "verify_134a2adb_upload.csv", 300)

    assert ur.expire_verify_uploads(str(tmp_path)) == 1
    assert not old.exists()
    assert fresh.exists() and customer_old.exists() and look_alike.exists() and nested.exists()


def test_a_ttl_of_zero_keeps_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_VERIFY_UPLOAD_TTL_HOURS", "0")
    old = _touch(tmp_path / "t1" / "verify_05d29aeb_upload.csv", 300)
    assert ur.expire_verify_uploads(str(tmp_path)) == 0 and old.exists()


def test_the_quota_counts_existing_files_plus_the_new_one(monkeypatch):
    monkeypatch.setenv("AURA_TENANT_STORAGE_QUOTA_MB", "1")
    mb = 1024 * 1024
    assert not ur.over_quota([mb // 2], mb // 2)
    assert ur.over_quota([mb // 2], mb // 2 + 1)
    monkeypatch.setenv("AURA_TENANT_STORAGE_QUOTA_MB", "0")
    assert not ur.over_quota([10 * mb], 10 * mb)


def test_an_upload_past_the_tenant_quota_is_refused_and_a_replacement_is_not(tmp_path, monkeypatch):
    import io

    from tests.test_files_storage import _client

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("AURA_TENANT_STORAGE_QUOTA_MB", str(300 / (1024 * 1024)))  # 300 bytes
    body = b"a,b\n" + b"1,2\n" * 40  # 164 bytes

    def upload(name):
        return client.post("/api/v1/upload", files={"file": (name, io.BytesIO(body), "text/csv")})

    assert upload("first.csv").status_code == 200
    second = upload("second.csv")
    assert second.status_code == 413 and "storage limit" in second.text
    # Re-uploading the same name replaces it, so it does not double-count.
    assert upload("first.csv").status_code == 200

    from shared.storage import get_storage_backend
    assert {o.name for o in get_storage_backend().list("default")} == {"first.csv"}


def test_catalog_sidecars_count_against_the_quota(tmp_path):
    side = tmp_path / ".aura_catalog"
    side.mkdir()
    (side / "a.csv.parquet").write_bytes(b"x" * 700)
    (side / "a.csv.json").write_bytes(b"x" * 300)
    assert ur.catalog_bytes(tmp_path) == 1000
    assert ur.catalog_bytes(tmp_path, replacing="a.csv") == 0
    assert ur.catalog_bytes(None) == 0
