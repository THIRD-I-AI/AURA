"""Audit round 6b, storage area: BUG-477 and BUG-478."""
from __future__ import annotations

import io

import pytest

from shared import upload_retention as ur
from tests.test_files_storage import _client

# ── BUG-477 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["q1.xls", "export.tsv", "notes.txt", "noext", ".aura_catalog"])
def test_bug477_an_upload_the_listing_cannot_show_is_refused_and_not_stored(tmp_path, monkeypatch, name):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/upload", files={"file": (name, io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert r.status_code == 415, r.text
    # Nothing was written, so nothing invisible costs quota.
    tenant_dir = next(tmp_path.iterdir(), None)
    stored = [] if tenant_dir is None else [p.name for p in tenant_dir.iterdir()]
    assert name not in stored


@pytest.mark.parametrize("name", ["sales.csv", "Book.XLSX", "d.parquet", "rows.json"])
def test_bug477_data_files_still_upload_and_list(tmp_path, monkeypatch, name):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/upload", files={"file": (name, io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert r.status_code == 200, r.text
    from shared.storage import get_storage_backend
    assert name in {o.name for o in get_storage_backend().list("default")}


# ── BUG-478 ──────────────────────────────────────────────────────────────────

def _sidecars(tmp_path):
    (tmp_path / "sales.csv").write_bytes(b"x" * 100)
    side = tmp_path / ".aura_catalog"
    side.mkdir()
    (side / "sales.csv.json").write_bytes(b"x" * 50)
    (side / "sales.csv.0123456789ab.parquet").write_bytes(b"x" * 400)
    (side / "sales.csv.backup.csv.json").write_bytes(b"x" * 30)
    (side / "sales.csv.backup.csv.ba9876543210.parquet").write_bytes(b"x" * 70)


def test_bug478_replacing_a_prefix_name_keeps_other_files_sidecars_in_the_count(tmp_path):
    _sidecars(tmp_path)
    # 'sales' is a different file: every sidecar of sales.csv still counts.
    assert ur.tenant_dir_bytes(tmp_path, "sales") == 650
    # Replacing sales.csv drops only its own sidecars, not sales.csv.backup.csv's.
    assert ur.catalog_bytes(tmp_path, replacing="sales.csv") == 100
    assert ur.catalog_bytes(tmp_path, replacing="sales.csv.backup.csv") == 450


def test_bug478_own_sidecars_including_in_flight_temp_copies_are_left_out(tmp_path):
    side = tmp_path / ".aura_catalog"
    side.mkdir()
    (side / "a.csv.json").write_bytes(b"x" * 10)
    (side / "a.csv.parquet").write_bytes(b"x" * 20)
    (side / "a.csv.0123456789ab.parquet").write_bytes(b"x" * 30)
    (side / "a.csv.0123456789ab.4242.99.parquet.tmp").write_bytes(b"x" * 40)
    assert ur.catalog_bytes(tmp_path) == 100
    assert ur.catalog_bytes(tmp_path, replacing="a.csv") == 0
