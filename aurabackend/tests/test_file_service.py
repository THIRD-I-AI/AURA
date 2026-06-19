"""
AURA File Service Tests
========================
Tests for file validation, upload, processing, listing, deletion, and profiling.
Uses mocked filesystem / UploadFile objects — no real disk I/O for upload tests.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.file_service import FileService

# ── Helpers ────────────────────────────────────────────────────────

def _make_upload(filename: str, content_type: str, size: int = 100) -> MagicMock:
    """Return a mock UploadFile."""
    m = MagicMock()
    m.filename = filename
    m.content_type = content_type
    m.size = size
    m.read = AsyncMock(return_value=b"fake-content")
    return m


# ── Constructor ────────────────────────────────────────────────────

class TestFileServiceInit:
    def test_creates_directories(self):
        """FileService.__init__ should create uploads/processed/temp dirs."""
        svc = FileService()
        assert svc.uploads_path.exists()
        assert svc.processed_path.exists()
        assert svc.temp_path.exists()
        assert svc.max_file_size == 25 * 1024 * 1024

    def test_supported_types(self):
        svc = FileService()
        assert ".csv" in svc.supported_types["text/csv"]
        assert ".json" in svc.supported_types["application/json"]
        assert ".xlsx" in svc.supported_types[
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ]
        assert ".parquet" in svc.supported_types["application/octet-stream"]


# ── Utilities ─────────────────────────────────────────────────────

class TestUtilities:
    def test_generate_file_id_is_uuid(self):
        svc = FileService()
        fid = svc.generate_file_id()
        uuid.UUID(fid)  # raises if not valid uuid

    def test_generate_file_id_unique(self):
        svc = FileService()
        ids = {svc.generate_file_id() for _ in range(50)}
        assert len(ids) == 50

    def test_calculate_file_hash(self):
        svc = FileService()
        h1 = svc.calculate_file_hash(b"hello")
        h2 = svc.calculate_file_hash(b"hello")
        h3 = svc.calculate_file_hash(b"world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA256 hex length


# ── Validation ────────────────────────────────────────────────────

class TestValidateFile:
    def test_csv_accepted(self):
        svc = FileService()
        f = _make_upload("data.csv", "text/csv")
        info = svc.validate_file(f)
        assert info["filename"] == "data.csv"
        assert info["file_extension"] == ".csv"

    def test_json_accepted(self):
        svc = FileService()
        f = _make_upload("data.json", "application/json")
        info = svc.validate_file(f)
        assert info["file_extension"] == ".json"

    def test_xlsx_accepted(self):
        svc = FileService()
        f = _make_upload("report.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        info = svc.validate_file(f)
        assert info["file_extension"] == ".xlsx"

    def test_parquet_by_extension(self):
        svc = FileService()
        f = _make_upload("data.parquet", "application/octet-stream")
        info = svc.validate_file(f)
        assert info["file_extension"] == ".parquet"

    def test_txt_accepted(self):
        svc = FileService()
        f = _make_upload("notes.txt", "text/plain")
        info = svc.validate_file(f)
        assert info["file_extension"] == ".txt"

    def test_unsupported_rejected(self):
        from fastapi import HTTPException
        svc = FileService()
        f = _make_upload("image.png", "image/png")
        with pytest.raises(HTTPException) as exc_info:
            svc.validate_file(f)
        assert exc_info.value.status_code == 415

    def test_accepted_by_extension_even_if_mime_unknown(self):
        svc = FileService()
        # A .csv file with a wrong MIME should still pass because extension matches
        f = _make_upload("data.csv", "application/unknown")
        info = svc.validate_file(f)
        assert info["file_extension"] == ".csv"


# ── save_file ─────────────────────────────────────────────────────

class TestSaveFile:
    @pytest.mark.asyncio
    async def test_save_file_returns_metadata(self, tmp_path):
        svc = FileService()
        svc.uploads_path = tmp_path

        f = _make_upload("data.csv", "text/csv")
        f.read = AsyncMock(return_value=b"col1,col2\n1,2\n3,4")

        with patch("aiofiles.open", return_value=AsyncMock()) as mock_aio:
            # Make the async context manager work
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_aio.return_value = mock_cm

            meta = await svc.save_file(f)

        assert "file_id" in meta
        assert meta["original_filename"] == "data.csv"
        assert meta["status"] == "uploaded"
        assert meta["file_extension"] == ".csv"
        assert "file_hash" in meta
        assert "upload_time" in meta


# ── list_files / delete_file ──────────────────────────────────────

class TestListAndDelete:
    def test_list_files(self, tmp_path, monkeypatch):
        # Point the storage backend at tmp_path so list_files picks up the
        # files we create here.  The backend slugs "default" as the tenant,
        # so files must live in tmp_path/default/.
        monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
        from shared.storage import reset_storage_backend
        reset_storage_backend()

        tenant_dir = tmp_path / "default"
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / "file1.csv").write_text("data")
        (tenant_dir / "file2.json").write_text("{}")

        svc = FileService()
        files = svc.list_files()
        assert len(files) == 2
        names = {f["filename"] for f in files}
        assert "file1.csv" in names
        assert "file2.json" in names
        for f in files:
            assert "size" in f
            assert "modified" in f  # present (None) — key still in dict

        reset_storage_backend()

    def test_list_files_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
        from shared.storage import reset_storage_backend
        reset_storage_backend()

        svc = FileService()
        assert svc.list_files() == []

        reset_storage_backend()

    def test_delete_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
        from shared.storage import reset_storage_backend
        reset_storage_backend()

        svc = FileService()
        svc.processed_path = tmp_path

        file_id = "test-id-123"
        # Write via the backend so list()/delete() can see the file.
        from shared.storage import get_storage_backend
        get_storage_backend().write("default", f"{file_id}.csv", b"data")
        (tmp_path / f"{file_id}_processed.json").write_text("{}")

        result = svc.delete_file(file_id)
        assert result is True
        assert not (tmp_path / "default" / f"{file_id}.csv").exists()
        assert not (tmp_path / f"{file_id}_processed.json").exists()

        reset_storage_backend()

    def test_delete_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
        from shared.storage import reset_storage_backend
        reset_storage_backend()

        svc = FileService()
        svc.processed_path = tmp_path
        # Nothing in the backend — delete returns False (nothing deleted).
        result = svc.delete_file("does-not-exist")
        assert result is False

        reset_storage_backend()

    def test_get_file_info_returns_none(self):
        svc = FileService()
        assert svc.get_file_info("nonexistent") is None


# ── Profiling ─────────────────────────────────────────────────────

class TestProfileDataframe:
    def test_numeric_profile(self):
        svc = FileService()
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [10.0, 20.0, 30.0, 40.0, 50.0]})
        profile = svc._profile_dataframe(df)
        assert profile["rows"] == 5
        assert profile["columns"] == 2
        assert "a" in profile["columns_profile"]
        assert profile["columns_profile"]["a"]["inferred_type"] == "numeric"
        assert profile["columns_profile"]["a"]["non_null"] == 5
        assert profile["columns_profile"]["a"]["nulls"] == 0
        assert "min" in profile["columns_profile"]["a"]
        assert "max" in profile["columns_profile"]["a"]
        assert "mean" in profile["columns_profile"]["a"]

    def test_categorical_profile(self):
        svc = FileService()
        df = pd.DataFrame({"name": ["Alice", "Bob", "Charlie", "Alice", "Bob"]})
        profile = svc._profile_dataframe(df)
        col = profile["columns_profile"]["name"]
        assert col["inferred_type"] == "categorical"
        assert col["distinct"] == 3
        assert "top_values" in col

    def test_with_nulls(self):
        svc = FileService()
        df = pd.DataFrame({"x": [1, None, 3, None, 5]})
        profile = svc._profile_dataframe(df)
        col = profile["columns_profile"]["x"]
        assert col["nulls"] == 2
        assert col["non_null"] == 3

    def test_empty_dataframe(self):
        svc = FileService()
        df = pd.DataFrame({"a": pd.Series(dtype="float64")})
        profile = svc._profile_dataframe(df)
        assert profile["rows"] == 0
        assert profile["columns"] == 1


# ── Dtype inference ───────────────────────────────────────────────

class TestInferDtype:
    def test_numeric(self):
        svc = FileService()
        assert svc._infer_dtype(pd.Series([1, 2, 3])) == "numeric"

    def test_datetime(self):
        svc = FileService()
        s = pd.to_datetime(pd.Series(["2024-01-01", "2024-02-01"]))
        assert svc._infer_dtype(s) == "datetime"

    def test_categorical(self):
        svc = FileService()
        assert svc._infer_dtype(pd.Series(["a", "b", "c"])) == "categorical"


# ── Serialization helper ─────────────────────────────────────────

class TestToSerializable:
    def test_numpy_scalar(self):
        svc = FileService()
        val = np.int64(42)
        result = svc._to_serializable(val)
        assert result == 42
        assert isinstance(result, int)

    def test_pandas_timestamp(self):
        svc = FileService()
        ts = pd.Timestamp("2024-01-15 10:30:00")
        result = svc._to_serializable(ts)
        assert isinstance(result, str)
        assert "2024-01-15" in result

    def test_plain_value(self):
        svc = FileService()
        assert svc._to_serializable(42) == 42
        assert svc._to_serializable("hello") == "hello"


# ── process_file (CSV path) ──────────────────────────────────────

class TestProcessFile:
    @pytest.mark.asyncio
    async def test_process_csv(self, tmp_path):
        svc = FileService()
        svc.processed_path = tmp_path

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25\n")

        metadata = {
            "file_id": "abc123",
            "file_path": str(csv_file),
            "file_extension": ".csv",
        }
        result = await svc.process_file(metadata)
        assert result["status"] == "processed"
        assert result["rows_count"] == 2
        assert result["columns_count"] == 2
        assert "profile" in result
        assert "preview_data" in result

    @pytest.mark.asyncio
    async def test_process_json_list(self, tmp_path):
        svc = FileService()
        svc.processed_path = tmp_path

        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]))

        metadata = {
            "file_id": "json1",
            "file_path": str(json_file),
            "file_extension": ".json",
        }
        result = await svc.process_file(metadata)
        assert result["status"] == "processed"
        assert result["rows_count"] == 2

    @pytest.mark.asyncio
    async def test_process_json_dict(self, tmp_path):
        svc = FileService()
        svc.processed_path = tmp_path

        json_file = tmp_path / "single.json"
        json_file.write_text(json.dumps({"key": "value", "num": 42}))

        metadata = {
            "file_id": "json2",
            "file_path": str(json_file),
            "file_extension": ".json",
        }
        result = await svc.process_file(metadata)
        assert result["status"] == "processed"
        assert result["rows_count"] == 1
        assert result["columns_count"] == 2

    @pytest.mark.asyncio
    async def test_process_txt_csv_like(self, tmp_path):
        svc = FileService()
        svc.processed_path = tmp_path

        txt_file = tmp_path / "data.txt"
        txt_file.write_text("col1,col2\n10,20\n30,40\n")

        metadata = {
            "file_id": "txt1",
            "file_path": str(txt_file),
            "file_extension": ".txt",
        }
        result = await svc.process_file(metadata)
        assert result["status"] == "processed"
        assert result["rows_count"] == 2

    @pytest.mark.asyncio
    async def test_process_txt_json(self, tmp_path):
        svc = FileService()
        svc.processed_path = tmp_path

        txt_file = tmp_path / "data.txt"
        txt_file.write_text(json.dumps([{"x": 1}, {"x": 2}]))

        metadata = {
            "file_id": "txt2",
            "file_path": str(txt_file),
            "file_extension": ".txt",
        }
        result = await svc.process_file(metadata)
        assert result["status"] == "processed"
        assert result["rows_count"] == 2

    @pytest.mark.asyncio
    async def test_process_error_raises(self, tmp_path):
        from fastapi import HTTPException
        svc = FileService()
        svc.processed_path = tmp_path

        metadata = {
            "file_id": "bad1",
            "file_path": "/nonexistent/path/file.csv",
            "file_extension": ".csv",
        }
        with pytest.raises(HTTPException) as exc_info:
            await svc.process_file(metadata)
        assert exc_info.value.status_code == 500
