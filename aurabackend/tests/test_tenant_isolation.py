import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: I001

from shared.file_service import FileService


def test_list_files_is_scoped_to_subdir(tmp_path, monkeypatch):
    fs = FileService()
    monkeypatch.setattr(fs, "uploads_path", tmp_path)
    (tmp_path / "orgA").mkdir()
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "a.csv").write_text("x")
    (tmp_path / "orgB" / "b.csv").write_text("y")
    names_a = {f["filename"] for f in fs.list_files(subdir="orgA")}
    assert names_a == {"a.csv"}
    assert "b.csv" not in names_a


import asyncio  # noqa: E402
import pathlib  # noqa: E402

import duckdb  # noqa: E402

from shared.data_utils import build_schema_context_cached  # noqa: E402


def test_schema_context_is_tenant_scoped(tmp_path):
    (tmp_path / "orgA").mkdir()
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "sales.csv").write_text("id,amt\n1,10\n")
    (tmp_path / "orgB" / "secret.csv").write_text("id,ssn\n1,999\n")

    async def run(d):
        con = duckdb.connect(":memory:")
        return await build_schema_context_cached(con, [pathlib.Path(d)], use_llm=False)

    a = asyncio.run(run(tmp_path / "orgA"))
    assert "sales" in a["tables"]
    assert "secret" not in a["tables"]
