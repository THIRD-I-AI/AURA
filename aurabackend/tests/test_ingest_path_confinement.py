"""BUG-441: file paths handed to the agents and to connectors /ingest stay inside the uploads."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import connectors.main as connectors_main


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    (root / "tenant-a").mkdir(parents=True)
    (root / "tenant-a" / "payroll.csv").write_text("name,salary\nalice,10\n")
    (tmp_path / "secret.csv").write_text("key,value\nroot,hunter2\n")
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(root))
    return root


def test_ingest_refuses_a_path_outside_the_uploads_root(uploads, tmp_path):
    client = TestClient(connectors_main.app)
    outside = str(tmp_path / "secret.csv")

    r = client.post("/ingest", json={"file_path": outside})

    assert r.status_code == 404, r.text
    assert outside not in r.text and "hunter2" not in r.text


def test_ingest_refuses_a_traversal_out_of_the_uploads_root(uploads):
    r = TestClient(connectors_main.app).post("/ingest", json={"file_path": "tenant-a/../../secret.csv"})
    assert r.status_code == 404, r.text


def test_ingest_still_profiles_a_file_in_the_uploads(uploads):
    r = TestClient(connectors_main.app).post("/ingest", json={"file_path": "tenant-a/payroll.csv"})
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["name", "salary"]


def _own_paths(files, own_dir, monkeypatch):
    from agents import api as agents_api
    from api_gateway.routers import workspaces

    monkeypatch.setattr(workspaces, "tenant_upload_dir", lambda request: str(own_dir))
    return agents_api._own_upload_paths(files, request=None)


def test_agent_files_may_only_name_the_callers_own_uploads(uploads, monkeypatch):
    own = uploads / "tenant-b"
    own.mkdir()
    (own / "mine.csv").write_text("a\n1\n")

    assert _own_paths(["mine.csv"], own, monkeypatch) == [str((own / "mine.csv").resolve())]
    for theirs in (str(uploads / "tenant-a" / "payroll.csv"), "../tenant-a/payroll.csv", ""):
        with pytest.raises(HTTPException) as refused:
            _own_paths([theirs], own, monkeypatch)
        assert refused.value.status_code == 400
