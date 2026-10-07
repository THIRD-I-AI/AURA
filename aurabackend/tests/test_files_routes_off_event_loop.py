"""BUG-337: GET /files, GET /files/{id}, GET /files/{id}/profile and DELETE /files/{id}
called the storage backend synchronously inside async handlers -- blocking boto3
round trips on S3 -- stalling every other request on the single worker."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api_gateway.routers import files as files_router


def _on_event_loop() -> bool:
    """True when called from code running inside the event loop's own thread."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture()
def client_and_calls(monkeypatch):
    calls = {}

    class RecordingFileService:
        def list_files(self, subdir=None):
            calls["list"] = _on_event_loop()
            return []

        def get_file_info(self, file_id, subdir=None):
            calls["info"] = _on_event_loop()
            return {"id": file_id, "name": file_id}

        def delete_file(self, file_id, subdir=None):
            calls["delete"] = _on_event_loop()
            return True

    monkeypatch.setattr(files_router, "file_service", RecordingFileService())
    from api_gateway.main import app

    return TestClient(app), calls


def test_listing_info_and_delete_run_off_the_event_loop(client_and_calls):
    client, calls = client_and_calls

    assert client.get("/api/v1/files").status_code == 200
    assert client.get("/api/v1/files/a.csv").status_code == 200
    client.delete("/api/v1/files/a.csv")

    assert set(calls) >= {"list", "info", "delete"}
    assert not any(calls.values()), f"ran on the event loop: {[k for k, v in calls.items() if v]}"
